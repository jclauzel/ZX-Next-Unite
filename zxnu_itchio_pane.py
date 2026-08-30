"""zxnu_itchio_pane.py — itch.io tab builder (optional, needs itch-dl).

Strangler extraction from MainWindow.__init__ (same builder-function seam as
the GetIt/ZXDB/zxArt/Unite!/Settings/NextSync panes): the whole itch.io tab
block — connection/auth row, collections combo, search, table/gallery stack,
the item viewer with install/uninstall/send-to-SD-card actions, cross-search
registration and the conditional tab insertion — now lives here as
build_itchio_pane(host, ...). The tab is only constructed when
zxnu_itchio.itchdl_available() reports the itch-dl package importable; the
host state attributes (_itchio_last_entries, _itchio_connected, ...) are
initialised unconditionally so the rest of the app can getattr() them.

Everything the block assigned to ``self`` is written to ``host`` (the
MainWindow), so every historical attribute keeps working; the __init__-locals
the block reads are injected as keyword-only params (forwarding lambdas at the
call site for names defined later in __init__, and the module-global
``right_disk_image_explorer_content`` read via the ``_right_disk_content``
getter hook). See CLAUDE.md and the memory ``strangler-extraction-pattern``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from PySide6.QtCore import (Qt, QTimer, QStringListModel)
from PySide6.QtGui import (QPixmap)
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QComboBox,
    QLineEdit, QGridLayout, QVBoxLayout, QSizePolicy, QScrollArea,
    QStackedWidget, QFrame, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QCompleter, QFileDialog, QInputDialog,
    QMessageBox)

from zxnu_config import *
from zxnu_i18n import ui_tr_now
from zxnu_api import *
from zxnu_gallery import *
from zxnu_media import *
from zxnu_workers import *
# Star imports skip underscore-prefixed names; import the private
# helpers the block uses explicitly (tests/test_pane_imports.py
# tripwires that these lists stay complete).
from zxnu_api import (_http_fetch_bytes_with_retry)
import zxnu_itchio


def build_itchio_pane(
    host,
    *,
    configuration_dictionary,
    save_configuration_file,
    image_upload_external_paths,
    generate_disk_file_path,
    _persist_retro,
    wid_inner,
    _right_disk_content,
    _CompleterPopupHider,
    _gallery_add_description_page,
    _gif_fetch_bytes,
    _make_retro_toggle_button,
    _qimage_from_data,
    _wrap_flow_row,
    getit_run_in_thread,
    _multi_search_enabled,
    _cross_search_getit,
    _cross_search_zxdb,
    _cross_search_zxart,
    _set_tab_badge,
    _clear_tab_badge,
    _start_tab_spinner,
    _stop_tab_spinner,
):
    """Build the itch.io tab (state, closures, widgets, conditional insert)."""
    # ─── ONLINE: itch.io Tab (optional, requires the 'itch-dl' package) ──
    # Browses the logged-in user's itch.io collections (the same content as
    # https://itch.io/my-collections) in a GalleryView, and installs a
    # selected item by delegating to itch-dl. Authentication is a personal
    # itch.io API key, stored in hdfg.cfg. Built only when itch-dl is
    # importable; the Settings toggle and saved preference control whether
    # the tab is shown. See zxnu_itchio.py for the API/install helpers.
    host._itchio_last_entries = []      # last collection/search results
    host._itchio_collections  = []
    host._itchio_owned        = set()   # game-id strings the user owns
    host._itchio_library      = None    # cached collections+purchases for search
    host._itchio_connected    = False   # True once a key has connected
    host._itchio_tab_widget   = None

    def _itchio_api_key():
        return (configuration_dictionary.get(SETTING_ITCHIO_API_KEY, "") or "").strip()

    def _itchio_downloads_dir():
        d = os.path.join(ZXNU_DATA_ROOT, DOWNLOADS_CSPECT_DIRNAME)
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d

    # Cache of normalised install-folder names, so the gallery can flag
    # every locally-downloaded cell without re-walking the downloads tree
    # per item. Invalidated (set to None) whenever an install/uninstall
    # changes what's on disk; rebuilt lazily on the next lookup.
    host._itchio_installed_names_cache = None

    def _itchio_installed_names():
        if host._itchio_installed_names_cache is None:
            try:
                host._itchio_installed_names_cache = (
                    zxnu_itchio.installed_dir_names(_itchio_downloads_dir()))
            except Exception:
                host._itchio_installed_names_cache = set()
        return host._itchio_installed_names_cache

    def _itchio_is_installed(entry):
        try:
            return zxnu_itchio.entry_installed(entry, _itchio_installed_names())
        except Exception:
            return False

    def _itchio_mark_local_state_changed():
        """Forget the cached install scan and re-flag the gallery cells so
        the 'Installed' badges reflect a fresh install/uninstall."""
        host._itchio_installed_names_cache = None
        try:
            host.itchio_gallery_view.refresh_installed_overlays()
        except Exception:
            pass
    host._itchio_mark_local_state_changed = _itchio_mark_local_state_changed

    if zxnu_itchio.itchdl_available()[0]:
        zxnextunite_itchio_tab = QWidget(wid_inner.tab)
        zxnextunite_itchio_tab.setAttribute(Qt.WA_TranslucentBackground)
        zxnextunite_itchio_tab.setAutoFillBackground(False)
        _itchio_grid = QGridLayout(zxnextunite_itchio_tab)

        host._itchio_stack = QStackedWidget()
        host._itchio_stack.setAttribute(Qt.WA_TranslucentBackground)
        host._itchio_stack.setAutoFillBackground(False)

        _itchio_main = QWidget()
        _itchio_v = QVBoxLayout(_itchio_main)
        _itchio_v.setContentsMargins(6, 6, 6, 6)

        # --- Authentication row (wraps when the window is narrow) ---
        _itchio_auth = FlowLayout(margin=2)
        _itchio_auth.addWidget(QLabel("itch.io API key:"))
        host.itchio_key_input = QLineEdit()
        host.itchio_key_input.setEchoMode(QLineEdit.Password)
        host.itchio_key_input.setPlaceholderText(
            "Paste your personal API key (itch.io → Settings → API keys)")
        host.itchio_key_input.setText(_itchio_api_key())
        host.itchio_key_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        _itchio_auth.addWidget(host.itchio_key_input)
        host.itchio_connect_button = QPushButton("Connect")
        _itchio_auth.addWidget(host.itchio_connect_button)
        host.itchio_getkey_button = QPushButton("Get API key…")
        host.itchio_getkey_button.setToolTip(
            "Open https://itch.io/user/settings/api-keys in your browser")
        _itchio_auth.addWidget(host.itchio_getkey_button)
        # Generic link to the itch.io website (always visible).
        host.itchio_site_link = QLabel(
            '<a href="https://itch.io/" style="color:#9cd2ff;">🌐 itch.io ↗</a>')
        host.itchio_site_link.setTextFormat(Qt.RichText)
        host.itchio_site_link.setOpenExternalLinks(True)
        host.itchio_site_link.setToolTip("Open https://itch.io/ in your browser")
        _itchio_auth.addWidget(host.itchio_site_link)
        _itchio_v.addWidget(_wrap_flow_row(_itchio_auth))

        # --- Collections row (wraps when the window is narrow) ---
        _itchio_crow = FlowLayout(margin=2)
        _itchio_crow.addWidget(QLabel("Collection:"))
        host.itchio_collection_combo = QComboBox()
        host.itchio_collection_combo.setMinimumWidth(260)
        host.itchio_collection_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        _itchio_crow.addWidget(host.itchio_collection_combo)
        host.itchio_refresh_button = QPushButton("Refresh")
        _itchio_crow.addWidget(host.itchio_refresh_button)
        _itchio_crow.addWidget(QLabel("View:"))
        host.itchio_view_combo = QComboBox()
        host.itchio_view_combo.addItem("Table",   "table")    # index 0
        host.itchio_view_combo.addItem("Gallery", "gallery")  # index 1
        host.itchio_view_combo.setToolTip(
            "Switch between the classic table view and the picture (gallery)\n"
            "view. Persisted across sessions in the config file.")
        _itchio_crow.addWidget(host.itchio_view_combo)
        host.itchio_retro_button = _make_retro_toggle_button(
            host, "_itchio_item_retro",
            on_change=lambda c, k=SETTING_ITCHIO_ITEM_RETRO: (
                _persist_retro(k, c), host._pane_retro_gallery_set("itchio", c)))
        _itchio_crow.addWidget(host.itchio_retro_button)
        _itchio_v.addWidget(_wrap_flow_row(_itchio_crow))

        # --- Search row (filters the library: collections + purchases) ---
        # Wraps the Search button onto a second line when the window is too
        # narrow to fit it beside the (expanding) input box.
        _itchio_srow = FlowLayout(margin=2)
        _itchio_srow.addWidget(QLabel("Search:"))
        host.itchio_search_input = QLineEdit()
        host.itchio_search_input.setPlaceholderText(
            "Search your itch.io library (collections + purchases)…")
        host.itchio_search_input.setClearButtonEnabled(True)
        host.itchio_search_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        _itchio_srow.addWidget(host.itchio_search_input)
        host.itchio_search_button = QPushButton("Search")
        _itchio_srow.addWidget(host.itchio_search_button)
        _itchio_v.addWidget(_wrap_flow_row(_itchio_srow))

        # Autocomplete over the library titles (populated once the library
        # is built after Connect).
        host._itchio_ac_model = QStringListModel(host)
        _itchio_completer = QCompleter(host._itchio_ac_model, host)
        _itchio_completer.setCompletionMode(QCompleter.PopupCompletion)
        _itchio_completer.setCaseSensitivity(Qt.CaseInsensitive)
        _itchio_completer.setFilterMode(Qt.MatchContains)
        _itchio_popup = _itchio_completer.popup()
        if _itchio_popup is not None:
            _itchio_popup.setParent(host)
            # Non-grabbing tool window (NOT Qt.Popup) so the auto-shown
            # completer popup never performs the implicit Windows mouse/
            # keyboard grab that can get stuck and leave the search box
            # unclickable.
            _itchio_popup.setWindowFlags(
                Qt.Tool | Qt.FramelessWindowHint
                | Qt.WindowStaysOnTopHint
                | Qt.WindowDoesNotAcceptFocus)
            _itchio_popup.setFocusPolicy(Qt.NoFocus)
            _itchio_popup.setAttribute(Qt.WA_ShowWithoutActivating)
        host._itchio_completer = _itchio_completer
        host.itchio_search_input.setCompleter(_itchio_completer)
        host._itchio_popup_hider = _CompleterPopupHider(
            host.itchio_search_input, _itchio_completer, host)

        def _itchio_update_completer():
            lib = host._itchio_library or []
            titles = sorted({(g.get("title") or "").strip()
                             for g in lib if g.get("title")})
            try:
                host._itchio_ac_model.setStringList(titles)
            except Exception:
                pass
        host._itchio_update_completer = _itchio_update_completer

        host.itchio_status_label = QLabel("")
        host.itchio_status_label.setStyleSheet("color: #aaa;")
        host.itchio_status_label.setWordWrap(True)
        _itchio_v.addWidget(host.itchio_status_label)

        def _itchio_set_status(msg):
            try:
                host.itchio_status_label.setText(str(msg))
            except Exception:
                pass

        # --- Async image + thumbnail helpers ---
        def _itchio_extra_fetch(url, on_pixmap):
            """Generic image-URL → QPixmap loader (cover art / screenshots)."""
            if not url or (isinstance(url, str) and url.startswith("placeholder://")):
                if isinstance(url, str) and url.startswith("placeholder://"):
                    rest = url[len("placeholder://"):]
                    label, _, sub = rest.partition("/")
                    pm = zxfmt_make_placeholder_pixmap(label or "itch.io", sub)
                    if not pm.isNull():
                        on_pixmap(pm)
                return
            def _fn(_u=url):
                tmp = tempfile.NamedTemporaryFile(suffix=".img", delete=False)
                tmp.close()
                with open(tmp.name, "wb") as _fh:
                    _fh.write(_http_fetch_bytes_with_retry(_u, timeout=20))
                return tmp.name
            def _ok(path):
                px = QPixmap(path)
                try: os.unlink(path)
                except Exception: pass
                on_pixmap(px if not px.isNull() else None)
            def _err(_e):
                on_pixmap(None)
            getit_run_in_thread(_fn, _ok, _err)

        def _itchio_thumb_fetch(entry, set_pixmap, set_screenshots,
                                set_tags=None, set_info_text=None):
            cover = (entry.get("cover_url") or "").strip()
            title = entry.get("title") or entry.get("id") or ""
            def _placeholder():
                ph = f"placeholder://itch.io/{title}"
                set_screenshots([ph])
                pm = zxfmt_make_placeholder_pixmap("itch.io", title)
                if not pm.isNull():
                    set_pixmap(pm, ph)
            if not cover:
                _placeholder()
                return
            set_screenshots([cover])
            def _fn(_u=cover):
                # Fetch *and* decode off the UI thread (QImage is
                # thread-safe); the UI callback only does QPixmap.fromImage.
                data = _http_fetch_bytes_with_retry(_u, timeout=20)
                return (_qimage_from_data(data), _u)
            def _ok(res, _set=set_pixmap):
                img, u = res
                px = QPixmap.fromImage(img) if (img is not None and not img.isNull()) else QPixmap()
                if px.isNull():
                    _placeholder()
                else:
                    _set(px, u)
            def _err(_e):
                _placeholder()
            getit_run_in_thread(_fn, _ok, _err, gated=True)

        def _itchio_title_getter(e):
            return str(e.get("title") or e.get("id") or "")

        def _itchio_info_getter(e):
            parts = []
            if e.get("author"):
                parts.append(str(e["author"]))
            if e.get("classification"):
                parts.append(str(e["classification"]))
            return " · ".join(parts)

        host.itchio_gallery_view = GalleryView(
            rows_per_page_getter=lambda: host._gallery_rows_per_page,
            anim_mode_getter=lambda: host._gallery_anim_mode,
            cols_getter=lambda: host._gallery_cols,
            img_size_getter=lambda: host._gallery_img_size,
            thumb_fetch_cb=_itchio_thumb_fetch,
            extra_fetch_cb=_itchio_extra_fetch,
            title_getter=_itchio_title_getter,
            info_getter=_itchio_info_getter,
            source_label_getter=lambda _e: "itch.io",
            source_overlay_anchor="bottomleft",
            installed_getter=_itchio_is_installed,
            is_favorite_cb=lambda e: host._fav_is({**e, "_fav_source": "itchio"}),
            toggle_favorite_cb=lambda e: host._fav_toggle({**e, "_fav_source": "itchio"}),
        )
        # Animate .gif thumbnails (QMovie) just like the in-pane item viewer.
        host.itchio_gallery_view.set_gif_fetch_cb(_gif_fetch_bytes)
        # Table view (index 0) flipped via the View combo; default Gallery.
        host.itchio_results_table = QTableWidget(0, 3)
        host.itchio_results_table.setHorizontalHeaderLabels(
            ["Title", "Author", "Type"])
        host.itchio_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        host.itchio_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        host.itchio_results_table.setSelectionMode(QAbstractItemView.SingleSelection)
        host.itchio_results_table.verticalHeader().setVisible(False)
        try:
            _ihh = host.itchio_results_table.horizontalHeader()
            _ihh.setStretchLastSection(False)
            _ihh.setSectionResizeMode(0, QHeaderView.Stretch)
            _ihh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            _ihh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        except Exception:
            pass

        host.itchio_view_stack = QStackedWidget()
        host.itchio_view_stack.addWidget(host.itchio_results_table)  # idx 0 = table
        host.itchio_view_stack.addWidget(host.itchio_gallery_view)   # idx 1 = gallery
        _itchio_v.addWidget(host.itchio_view_stack, 1)
        # Wrap the main page in a scroll area so a vertical scrollbar appears
        # when the tab is too short for its content — matching the GetIt /
        # ZXDB / zxArt tabs (which lack one here otherwise).
        _itchio_scroll = QScrollArea()
        _itchio_scroll.setWidget(_itchio_main)
        _itchio_scroll.setWidgetResizable(True)
        _itchio_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        _itchio_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        _itchio_scroll.setFrameShape(QFrame.NoFrame)
        _itchio_scroll.setAutoFillBackground(False)
        _itchio_scroll.setAttribute(Qt.WA_TranslucentBackground)
        _itchio_scroll.viewport().setAutoFillBackground(False)
        _itchio_scroll.viewport().setAttribute(Qt.WA_TranslucentBackground)
        host._itchio_stack.addWidget(_itchio_scroll)   # index 0

        def _itchio_table_entry_for_row(row):
            if row < 0 or row >= host.itchio_results_table.rowCount():
                return None
            it = host.itchio_results_table.item(row, 0)
            e = it.data(Qt.UserRole) if it is not None else None
            return e if isinstance(e, dict) else None

        def _itchio_fill_table(entries):
            t = host.itchio_results_table
            t.setRowCount(0)
            for e in entries:
                r = t.rowCount()
                t.insertRow(r)
                it0 = QTableWidgetItem(str(e.get("title") or e.get("id") or ""))
                it0.setData(Qt.UserRole, e)
                t.setItem(r, 0, it0)
                t.setItem(r, 1, QTableWidgetItem(str(e.get("author") or "")))
                t.setItem(r, 2, QTableWidgetItem(str(e.get("classification") or "")))

        def _itchio_populate(entries):
            """Render results into both the gallery and the table views."""
            host._itchio_last_entries = list(entries)
            # Re-scan local installs so the "Installed" badges are accurate
            # for this fresh batch (cheap: one bounded walk, then cached).
            host._itchio_installed_names_cache = None
            host.itchio_gallery_view.populate(entries)
            _itchio_fill_table(entries)
            host._pane_retro_gallery_refresh("itchio")
        host._itchio_populate = _itchio_populate

        def _itchio_table_dbl(_idx):
            e = _itchio_table_entry_for_row(host.itchio_results_table.currentRow())
            if e is not None:
                host._pane_open_item("itchio", e, getattr(host, "_itchio_item_retro", False))
        host.itchio_results_table.doubleClicked.connect(_itchio_table_dbl)

        def _itchio_apply_view_mode(mode, *, persist=True):
            mode = (mode or "gallery").lower()
            if mode not in ("table", "gallery"):
                mode = "gallery"
            host._itchio_view_mode = mode
            host.itchio_view_stack.setCurrentIndex(0 if mode == "table" else 1)
            if getattr(host, "_pane_retro_gallery_refresh", None):
                host._pane_retro_gallery_refresh("itchio")
            cb = host.itchio_view_combo
            target = 0 if mode == "table" else 1
            if cb.currentIndex() != target:
                cb.blockSignals(True)
                cb.setCurrentIndex(target)
                cb.blockSignals(False)
            if persist:
                # Sync the shared view mode across all online panes.
                if hasattr(host, '_getit_apply_view_mode'):
                    host._getit_apply_view_mode(mode, persist=False)
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
                configuration_dictionary[SETTING_ITCHIO_VIEW_MODE]    = mode
                save_configuration_file()
        host._itchio_apply_view_mode = _itchio_apply_view_mode
        host.itchio_view_combo.currentIndexChanged.connect(
            lambda _i: _itchio_apply_view_mode(
                host.itchio_view_combo.currentData() or "gallery"))
        # Apply the default now (Gallery); the saved preference is re-applied
        # after the config file is loaded.
        _itchio_apply_view_mode(host._itchio_view_mode, persist=False)

        # Register fetchers so the Unite! aggregated gallery can render and
        # open itch.io items too.
        host._fav_fetchers = getattr(host, "_fav_fetchers", {})
        host._fav_fetchers["itchio"] = {
            "thumb": _itchio_thumb_fetch,
            "extra": _itchio_extra_fetch,
            "title": _itchio_title_getter,
            "info":  _itchio_info_getter,
            # An itch.io entry has a picture only when it carries a cover URL.
            "has_image": lambda e: bool((e.get("cover_url") or "").strip()),
        }

        # --- The in-pane item viewer with an Install button + status ---
        def _itchio_open_gallery_viewer(entry, make_viewer=None, install=True):
            if not isinstance(entry, dict):
                return None
            title = entry.get("title") or entry.get("id") or ""
            gid   = str(entry.get("id") or "")
            cover = (entry.get("cover_url") or "").strip()
            dest  = _itchio_downloads_dir()
            owned = gid in (host._itchio_owned or set())  # set membership, no I/O

            def _status_text(installed_flag):
                bits = ["Owned" if owned else "Not in your library"]
                if installed_flag is None:
                    bits.append("checking download status…")
                else:
                    bits.append("Downloaded locally" if installed_flag
                                else "Not downloaded")
                return " · ".join(bits)

            base_rows = [
                ("Title:",   title),
                ("Author:",  entry.get("author", "")),
                ("Type:",    entry.get("classification", "")),
                ("About:",   entry.get("short_text", "")),
                ("itch.io:", entry.get("url", "")),
            ]
            # Build the viewer immediately; the local "is it already
            # downloaded?" disk scan is resolved on a worker thread below so
            # opening an item never blocks the UI thread.
            info_rows = base_rows + [("Status:", _status_text(None))]

            _mk = make_viewer or (lambda **kw: GalleryItemViewer(
            parent=host, anim_mode_getter=lambda: host._gallery_anim_mode, **kw))
            viewer = _mk(
                title=title,
                info_rows=info_rows,
                screenshots=[cover] if cover else [],
                extra_fetch_cb=_itchio_extra_fetch,
            )
            if hasattr(viewer, "set_gif_fetch_cb"):
                viewer.set_gif_fetch_cb(_gif_fetch_bytes)
            viewer.set_placeholder("itch.io", title)
            _fav_entry_itchio = {**entry, "_fav_source": "itchio"}
            viewer.set_favorite_hooks(_fav_entry_itchio, host._fav_is, host._fav_toggle)
            viewer.set_open_web_url(entry.get("url", ""), "itch.io")
            # "Launch CSpect" / "Launch Mame" under "Send to SD card",
            # matching the GetIt/ZXDB/ZxArt viewers (shown when the emulator
            # exists, greyed out until a disk image is ready).
            host._wire_viewer_emulators(viewer)
            viewer._itchio_busy = False   # True while an install runs

            # itch.io items are game uploads (zip/exe), not standalone text
            # files, so surface the item's About/description as the readable
            # Pygame log-console page (no-op for the Qt viewer).
            _gallery_add_description_page(
                viewer, entry.get("short_text") or entry.get("description"),
                label="About")

            def _itchio_open_install_folder(_=False, _e=entry):
                """Open the item's install folder (or the itch.io downloads
                root if the exact folder can't be resolved) in the OS file
                explorer."""
                p = zxnu_itchio.installed_path(_e, dest) or dest
                try:
                    if not os.path.isdir(p):
                        p = dest
                    os.makedirs(p, exist_ok=True)
                except OSError:
                    pass
                if not os.path.isdir(p):
                    return
                try:
                    if sys.platform == "win32":
                        os.startfile(p)
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", p])
                    else:
                        subprocess.Popen(["xdg-open", p])
                except Exception:
                    pass

            def _itchio_send_to_image(_=False, _e=entry):
                """Send an installed itch.io item to the loaded SD-card image:
                hdfmonkey-put the whole install folder into the image at the
                current browse path. Mirrors the GetIt/ZXDB 'Send to SD card'
                action (which sends downloaded content into the image)."""
                path = zxnu_itchio.installed_path(_e, dest)
                if not path or not os.path.isdir(path):
                    _itchio_set_status("Install this item before sending to SD card.")
                    return
                if not _right_disk_content() or not host.right_disk_image_path:
                    _itchio_set_status("Please load a disk image first (SD Card tab).")
                    return
                target_dir = generate_disk_file_path()
                _itchio_set_status(f"Sending “{title}” to the SD card image…")
                def _done(ok_flag):
                    if ok_flag:
                        host._show_sd_notification(
                            ui_tr_now("Sent to SD card image:\n{name}").format(
                                name=os.path.basename(path)))
                        _itchio_set_status(f"Sent “{title}” to the SD card image.")
                # image_upload_external_paths shows its own progress dialog and
                # uploads files/folders recursively.
                image_upload_external_paths([path], target_dir, on_complete=_done)

            def _itchio_send_via_nextsync(_=False, _e=entry):
                """Send an installed itch.io item to a real Spectrum Next via
                NextSync: start the NextSync server serving the item's install
                folder. Mirrors the GetIt/ZXDB 'Send via NextSync' action."""
                path = zxnu_itchio.installed_path(_e, dest)
                if not path or not os.path.isdir(path):
                    _itchio_set_status("Install this item before sending via NextSync.")
                    return
                _itchio_set_status(f"Starting NextSync for “{title}”…")
                QTimer.singleShot(0, lambda _f=path: host._nextsync_start_server_fn(_f))

            # The item viewer is either the Qt GalleryItemViewer (Classic
            # mode, exposes btn_* QPushButtons) or the pygame PygameItemViewer
            # (Unite! pygame mode, exposes an internal _buttons dict). These
            # helpers drive whichever one we got so opening works in both.
            def _itchio_label_button(_v, key, text):
                # One chokepoint for BOTH viewers (Qt btn_<key>.setText and the
                # pygame _buttons[key].label), so translating here covers every
                # caller's label — Install / Re-install / Installing… etc.
                text = ui_tr_now(text)
                btn = getattr(_v, "btn_" + key, None)
                if btn is not None:
                    try: btn.setText(text)
                    except RuntimeError: pass
                    return
                btns = getattr(_v, "_buttons", None)
                if isinstance(btns, dict) and key in btns:
                    try:
                        btns[key].label = text
                        _v.redraw()
                    except Exception:
                        pass

            def _itchio_enable_download(_v, on):
                btn = getattr(_v, "btn_download", None)
                if btn is not None:
                    try: btn.setEnabled(bool(on))
                    except RuntimeError: pass

            def _itchio_btn_set(_v, key, *, enabled=None, visible=None,
                                tooltip=None, text=None):
                """Update an action button by key in either viewer mode:
                Classic (Qt ``btn_<key>``) or pygame (``_buttons[key]``).
                Only the passed attributes are changed."""
                btn = getattr(_v, "btn_" + key, None)
                if btn is not None:
                    try:
                        if text is not None: btn.setText(text)
                        if enabled is not None: btn.setEnabled(bool(enabled))
                        if visible is not None: btn.setVisible(bool(visible))
                        if tooltip is not None: btn.setToolTip(tooltip)
                    except RuntimeError:
                        pass
                    return
                btns = getattr(_v, "_buttons", None)
                if isinstance(btns, dict) and key in btns:
                    b = btns[key]
                    try:
                        if text is not None: b.label = text
                        if enabled is not None: b.enabled = bool(enabled)
                        if visible is not None: b.visible = bool(visible)
                        if tooltip is not None: b.tooltip = tooltip
                        _v.redraw()
                    except Exception:
                        pass

            def _itchio_refresh_install_buttons(_v, installed_flag):
                """Update the action buttons for the current download state
                (works in Classic and pygame modes). The three primary slots
                mirror GetIt/ZXDB — Install / Send to SD card / Send via
                NextSync — and the Send buttons are enabled only once the item
                is installed locally (Send to SD also needs a loaded image)."""
                _itchio_label_button(
                    _v, "download",
                    "✓  Re-install" if installed_flag else "⬇  Install")
                _inst_path = ""
                try:
                    _inst_path = zxnu_itchio.installed_path(entry, dest) or ""
                except Exception:
                    _inst_path = ""
                _img_ready = bool(host.right_disk_image_path) and bool(
                    _right_disk_content())
                # Send to SD card: needs an install and a loaded disk image.
                _itchio_btn_set(
                    _v, "send_sd",
                    enabled=bool(installed_flag) and _img_ready,
                    tooltip=("Install this item first" if not installed_flag
                             else ("Load a disk image first (SD Card tab)"
                                   if not _img_ready
                                   else f"Send {os.path.basename(_inst_path)} → image")))
                # Send via NextSync: needs an install.
                _itchio_btn_set(
                    _v, "send_ns",
                    enabled=bool(installed_flag),
                    tooltip=("Install this item first" if not installed_flag
                             else f"Serve {_inst_path} via NextSync"))
                # Uninstall: shown only when a local copy exists.
                _itchio_btn_set(
                    _v, "uninstall",
                    visible=bool(installed_flag),
                    enabled=bool(installed_flag),
                    tooltip=(f"Delete {_inst_path} and its contents"
                             if installed_flag else ""))
                # Open-folder shortcut at the bottom; label tracks state.
                _itchio_btn_set(
                    _v, "open_folder",
                    text=("📂  Open install folder" if installed_flag
                          else "📂  Open download folder"),
                    tooltip=(_inst_path or dest))

            def _chk_fn(_e=entry, _d=dest):
                return zxnu_itchio.installed_status(_e, _d)
            def _chk_ok(installed_flag, _v=viewer):
                # Don't clobber an install that the user has already started.
                if getattr(_v, "_itchio_busy", False):
                    return
                _itchio_refresh_install_buttons(_v, installed_flag)
                try:
                    _v.refresh_meta(
                        title, base_rows + [("Status:", _status_text(installed_flag))])
                except Exception:
                    pass
            def _chk_err(_e):
                pass
            getit_run_in_thread(_chk_fn, _chk_ok, _chk_err)

            def _itchio_run_setup_extract(_e=entry):
                """Post-install setup: extract any .zip archives shipped in
                the item's ``files`` folder (e.g. a CSpect build) into a
                per-archive subfolder. When several archives are present, ask
                the user which one to extract via a drop-down (OK/Cancel,
                defaulting to the highest version). For a CSpect install we
                also re-run emulator detection afterwards so the freshly
                downloaded CSpect/hdfmonkey are usable without a restart."""
                is_cspect = "cspect" in (
                    (_e.get("url") or "") + " " + (_e.get("title") or "")
                ).lower()

                def _redetect():
                    if is_cspect:
                        # Arm the Windows-only "install OpenAL for sound"
                        # notice appended to the detection toast that the
                        # rescan triggers once CSpect is found.
                        host._cspect_openal_notice_pending = True
                        try:
                            host._rescan_emulators_after_install()
                        except Exception:
                            pass

                try:
                    install_path = zxnu_itchio.installed_path(_e, dest)
                    zips = zxnu_itchio.find_extractable_zips(install_path)
                except Exception:
                    zips = []
                if not zips:
                    # Nothing new to extract (or already extracted) — still
                    # re-detect for CSpect so a prior build is picked up.
                    _redetect()
                    return
                if len(zips) == 1:
                    chosen = zips[0]
                else:
                    # zips is sorted highest version first, so index 0 is the
                    # latest build by default.
                    names = [os.path.basename(z) for z in zips]
                    name, ok = QInputDialog.getItem(
                        host, "Extract download",
                        "This download contains several archives.\n"
                        "Choose which one to extract:",
                        names, 0, False)
                    if not ok:
                        return
                    chosen = zips[names.index(name)]

                # Extract on a worker thread so a large archive can't freeze
                # the UI.
                def _xfn(_zp=chosen):
                    return zxnu_itchio.extract_zip(_zp)
                def _xok(out):
                    _itchio_set_status(f"Extracted to {out}")
                    _redetect()
                def _xerr(e):
                    _itchio_set_status(f"Extraction failed: {e}")
                _itchio_set_status(f"Extracting {os.path.basename(chosen)}…")
                getit_run_in_thread(_xfn, _xok, _xerr)

            def _itchio_pick_and_install_zip(_e=entry):
                """Let the user install from a .zip they downloaded manually
                from the itch.io page (the browser fallback). The archive is
                copied into the item's install folder, extracted into a
                build-numbered subfolder, and emulator detection re-run so a
                manually fetched CSpect becomes usable (and its group shows)."""
                fn, _sel = QFileDialog.getOpenFileName(
                    host, "Select the downloaded itch.io .zip", dest,
                    "Zip archives (*.zip)")
                if not fn:
                    return
                _itchio_set_status(f"Installing from {os.path.basename(fn)}…")
                def _fn(_g=_e, _zip=fn):
                    return zxnu_itchio.manual_install_zip(_g, _zip, dest)
                def _ok(out, _g=_e):
                    _itchio_set_status(f"Installed to {out}")
                    _itchio_mark_local_state_changed()
                    # Reveal the CSpect group if this was a CSpect build, and
                    # arm the Windows-only OpenAL sound notice for it.
                    _is_cspect = "cspect" in (
                        (_g.get("url") or "") + " " + (_g.get("title") or "")
                    ).lower()
                    if _is_cspect:
                        host._cspect_openal_notice_pending = True
                    if hasattr(host, "_rescan_emulators_after_install"):
                        try: host._rescan_emulators_after_install()
                        except Exception: pass
                def _err(e):
                    _itchio_set_status(f"Could not install from file: {e}")
                getit_run_in_thread(_fn, _ok, _err)

            def _itchio_offer_manual_fallback(_e=entry, _msg=""):
                """On a failed/blocked automated install, offer the manual
                browser download: open the itch.io page, or install from an
                already-downloaded .zip."""
                url = (_e.get("url") or "").strip()
                box = QMessageBox(host)
                box.setIcon(QMessageBox.Warning)
                box.setWindowTitle(ui_tr_now("itch.io download"))
                box.setText(_msg or ui_tr_now("The automated download failed."))
                box.setInformativeText(ui_tr_now(
                    "You can download it manually from the itch.io page in "
                    "your browser, then install it from the downloaded .zip."))
                open_btn = (box.addButton(ui_tr_now("Open itch.io page"),
                                          QMessageBox.ActionRole)
                            if url else None)
                pick_btn = box.addButton(
                    ui_tr_now("Install from .zip…"), QMessageBox.AcceptRole)
                box.addButton(ui_tr_now("Close"), QMessageBox.RejectRole)
                box.exec()
                clicked = box.clickedButton()
                if open_btn is not None and clicked is open_btn:
                    try: webbrowser.open(url, new=2)
                    except Exception: pass
                elif clicked is pick_btn:
                    _itchio_pick_and_install_zip(_e)

            def _install(_=False, _e=entry, _viewer=viewer):
                key = _itchio_api_key()
                if not key:
                    _itchio_set_status("Enter your itch.io API key and Connect first.")
                    try:
                        _viewer.refresh_meta(title, base_rows + [
                            ("Status:", "No API key — enter one on the itch.io tab.")])
                    except Exception:
                        pass
                    return
                try: _viewer._itchio_busy = True
                except RuntimeError: pass
                _itchio_enable_download(_viewer, False)
                _itchio_label_button(_viewer, "download", "⬇  Installing…")
                _itchio_set_status(f"Installing “{title}”…")

                # Shared completion handlers for whichever download path runs
                # (the chosen-version API download, or the itch-dl fallback).
                def _ok(res, _v=_viewer):
                    ok, msg = res
                    # The status label lives on the persistent tab, so it is
                    # always safe to update.
                    _itchio_set_status(msg)
                    # The viewer may have been closed while the install ran on
                    # the worker thread; every viewer touch below is guarded.
                    try: _v._itchio_busy = False
                    except RuntimeError: pass
                    _itchio_enable_download(_v, True)
                    _itchio_refresh_install_buttons(
                        _v, zxnu_itchio.installed_status(_e, dest) if ok else False)
                    try:
                        _v.refresh_meta(title, base_rows + [("Status:", msg)])
                    except Exception:
                        pass
                    # Post-install setup: extract any bundled .zip (CSpect
                    # builds and similar ship as archives under ``files``).
                    if ok:
                        _itchio_mark_local_state_changed()
                        _itchio_run_setup_extract(_e)
                    else:
                        # Automated download failed (e.g. itch.io's Cloudflare
                        # block on non-owned items). Offer the manual browser
                        # download fallback.
                        _itchio_offer_manual_fallback(_e, msg)
                def _err(e, _v=_viewer):
                    _itchio_set_status(f"Install failed: {e}")
                    try: _v._itchio_busy = False
                    except RuntimeError: pass
                    _itchio_enable_download(_v, True)
                    _itchio_label_button(_v, "download", "⬇  Install")
                    _itchio_offer_manual_fallback(_e, f"Install failed: {e}")

                def _reset_idle(_v=_viewer, status=None):
                    # The user cancelled the version picker: restore the idle
                    # Install button without treating it as a failure.
                    try: _v._itchio_busy = False
                    except RuntimeError: pass
                    _itchio_enable_download(_v, True)
                    _itchio_label_button(_v, "download", "⬇  Install")
                    if status:
                        _itchio_set_status(status)

                # Phase 1 (worker): list this item's downloadable files. itch.io
                # can expose several — CSpect ships every build (CSpect3_1_4_0,
                # CSpect3_1_3_0, …) side by side — so we may need to ask which to
                # fetch. Returns None for non-owned items (→ itch-dl fallback).
                def _list_fn(_g=_e, _k=key):
                    return zxnu_itchio.list_installable_uploads(_g, _k)

                def _list_ok(listed, _v=_viewer, _k=key):
                    if listed is None:
                        # Not owned via the API — fall back to the full
                        # install_game flow (itch-dl page scrape / backstop).
                        def _fb_fn(_g=_e, _kk=_k):
                            return zxnu_itchio.install_game(
                                _g, _kk, dest, log_cb=lambda ln: None)
                        getit_run_in_thread(_fb_fn, _ok, _err)
                        return
                    _game_id, key_id, uploads = listed
                    if len(uploads) == 1:
                        chosen = uploads[0]
                    else:
                        # Several downloadable versions — let the user pick,
                        # defaulting to the newest (index 0). This restores the
                        # version chooser that the single-upload API download
                        # otherwise bypassed.
                        names = [u["filename"] for u in uploads]
                        name, picked = QInputDialog.getItem(
                            host, "Choose version to install",
                            f"itch.io offers several versions of “{title}”.\n"
                            "Choose which one to download and install\n"
                            "(the newest is selected by default):",
                            names, 0, False)
                        if not picked:
                            _reset_idle(status=f"Install of “{title}” cancelled.")
                            return
                        chosen = uploads[names.index(name)]
                    _itchio_set_status(f"Downloading {chosen['filename']} …")
                    # Phase 2 (worker): download the chosen upload. A successful
                    # return means the file is on disk (download_owned_upload
                    # raises on any network failure), so no extra backstop is
                    # needed for this direct-API path.
                    def _dl_fn(_g=_e, _kk=_k, _u=chosen, _kid=key_id):
                        return zxnu_itchio.download_owned_upload(
                            _g, _kk, dest, _u, _kid, log_cb=lambda ln: None)
                    getit_run_in_thread(_dl_fn, _ok, _err)

                getit_run_in_thread(_list_fn, _list_ok, _err)

            def _itchio_uninstall(_=False, _e=entry, _viewer=viewer):
                """Delete this item's locally-downloaded copy — its install
                folder and everything under it — after an explicit
                confirmation. The deletion is irreversible, so the confirm
                dialog names the exact folder and defaults to Cancel."""
                path = zxnu_itchio.installed_path(_e, dest)
                if not path or not os.path.isdir(path):
                    _itchio_set_status("This item does not appear to be downloaded.")
                    _itchio_refresh_install_buttons(_viewer, False)
                    return
                reply = QMessageBox.question(
                    host, ui_tr_now("Uninstall"),
                    ui_tr_now(
                        "This is going to completely delete the files in "
                        "{path} and its sub folders, so they will be "
                        "unrecoverable.\n\nAre you sure want to continue?"
                    ).format(path=path),
                    QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
                if reply != QMessageBox.Yes:
                    return
                # CSpect ships as an itch.io package; removing it must
                # re-run emulator detection so the now-gone build stops
                # being offered (mirrors the post-install re-detect).
                is_cspect = "cspect" in (
                    (_e.get("url") or "") + " " + (_e.get("title") or "")
                ).lower()
                try: _viewer._itchio_busy = True
                except RuntimeError: pass
                _itchio_set_status(f"Uninstalling “{title}”…")
                def _fn(_p=path):
                    shutil.rmtree(_p)
                    return _p
                def _ok(_removed, _v=_viewer):
                    try: _v._itchio_busy = False
                    except RuntimeError: pass
                    _itchio_set_status(f"Uninstalled “{title}”.")
                    _itchio_refresh_install_buttons(_v, False)
                    _itchio_mark_local_state_changed()
                    try:
                        _v.refresh_meta(
                            title, base_rows + [("Status:", _status_text(False))])
                    except Exception:
                        pass
                    if is_cspect:
                        try:
                            host._rescan_emulators_after_uninstall(path)
                        except Exception:
                            pass
                def _err(e, _v=_viewer):
                    try: _v._itchio_busy = False
                    except RuntimeError: pass
                    _itchio_set_status(f"Uninstall failed: {e}")
                    _itchio_refresh_install_buttons(
                        _v, zxnu_itchio.installed_status(_e, dest))
                getit_run_in_thread(_fn, _ok, _err)

            # Wire actions through the abstract API so they work in both the
            # Classic (Qt) and pygame item viewers. The three primary slots
            # mirror GetIt/ZXDB — Install / Send to SD card / Send via
            # NextSync — and a dedicated Open-folder button sits at the bottom
            # (Classic viewer only). The Send buttons start disabled and are
            # enabled by _itchio_refresh_install_buttons once the async
            # installed-status check (or an install) confirms a local copy.
            viewer.set_actions(download_cb=_install,
                               send_sd_cb=_itchio_send_to_image,
                               send_ns_cb=_itchio_send_via_nextsync,
                               sd_enabled=False, ns_enabled=False)
            if hasattr(viewer, "set_uninstall_action"):
                # Hidden until the async installed-status check (or an
                # install) confirms a local copy; _itchio_refresh_install_buttons
                # reveals it.
                viewer.set_uninstall_action(_itchio_uninstall, visible=False)
            if hasattr(viewer, "set_open_folder_action"):
                viewer.set_open_folder_action(
                    _itchio_open_install_folder, "📂  Open download folder")
            # Neutral initial labels; the async status check upgrades them.
            _itchio_label_button(viewer, "download", "⬇  Install")
            _itchio_label_button(viewer, "send_sd", "💾  Send to SD card")
            _itchio_label_button(viewer, "send_ns", "🔁  Send via NextSync")

            if install:
                viewer.install_into_stack(
                    host._itchio_stack,
                    close_fn=lambda: host._itchio_stack.setCurrentIndex(0),
                )
            return viewer

        host._itchio_open_gallery_viewer = _itchio_open_gallery_viewer
        host.itchio_gallery_view.cell_dbl_clicked.connect(
            lambda e: host._pane_open_item("itchio", e, getattr(host, "_itchio_item_retro", False)))

        # --- Collection / search loading (async) ---
        def _itchio_load_collection_games(cid):
            key = _itchio_api_key()
            if not key or not cid:
                return
            _itchio_set_status("Loading collection…")
            def _fn(_c=cid, _k=key):
                return zxnu_itchio.collection_games(_k, _c)
            def _ok(games):
                _itchio_populate(games)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ITCHIO, len(games))
                _itchio_set_status(f"{len(games)} item(s) in this collection.")
                _aio = getattr(host, "_allinone_repopulate", None)
                if _aio is not None:
                    try: _aio()
                    except Exception: pass
            def _err(e):
                _itchio_set_status(f"itch.io: {e}")
            getit_run_in_thread(_fn, _ok, _err)
        host._itchio_load_collection_games = _itchio_load_collection_games

        # Sentinel stored as the combo's item data for the purchases entry.
        _ITCHIO_OWNED_KEY = "__owned__"

        def _itchio_load_owned_games():
            key = _itchio_api_key()
            if not key:
                return
            _itchio_set_status("Loading purchased / owned games…")
            def _fn(_k=key):
                return zxnu_itchio.owned_games(_k)
            def _ok(games):
                _itchio_populate(games)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ITCHIO, len(games))
                _itchio_set_status(f"{len(games)} purchased / owned game(s).")
                _aio = getattr(host, "_allinone_repopulate", None)
                if _aio is not None:
                    try: _aio()
                    except Exception: pass
            def _err(e):
                _itchio_set_status(f"itch.io: {e}")
            getit_run_in_thread(_fn, _ok, _err)
        host._itchio_load_owned_games = _itchio_load_owned_games

        # Sentinel for the creator entry: the user's OWN projects, drafts
        # included - GET /profile/games. This is what makes a not-yet-
        # published page visible to its author (the release-automation
        # groundwork): "purchased" and "collections" can never show it.
        _ITCHIO_CREATED_KEY = "__created__"

        def _itchio_load_created_games():
            key = _itchio_api_key()
            if not key:
                return
            _itchio_set_status("Loading your created projects…")
            def _fn(_k=key):
                return zxnu_itchio.created_games(_k)
            def _ok(games):
                _itchio_populate(games)
                drafts = sum(1 for g in games if not g.get("published"))
                extra = f" ({drafts} draft(s))" if drafts else ""
                _itchio_set_status(
                    f"{len(games)} created project(s){extra}.")
            def _err(e):
                _itchio_set_status(f"itch.io: {e}")
            getit_run_in_thread(_fn, _ok, _err)
        host._itchio_load_created_games = _itchio_load_created_games

        def _itchio_load_selection(data):
            """Load whichever combo entry is selected (a collection id, or
            the purchases sentinel)."""
            if data == _ITCHIO_OWNED_KEY:
                _itchio_load_owned_games()
            elif data == _ITCHIO_CREATED_KEY:
                _itchio_load_created_games()
            elif data:
                _itchio_load_collection_games(data)

        def _itchio_set_connecting(busy):
            """Toggle the busy state: while a connect/login is in flight the
            Connect/Refresh buttons are disabled so requests cannot overlap.
            The actual itch.io validation runs on a worker thread, so the UI
            stays responsive throughout."""
            host._itchio_connecting = bool(busy)
            try:
                host.itchio_connect_button.setEnabled(not busy)
                host.itchio_refresh_button.setEnabled(not busy)
            except RuntimeError:
                pass

        def _itchio_load_collections():
            # Guard against overlapping connects (e.g. the startup
            # auto-connect racing a quick manual click).
            if getattr(host, "_itchio_connecting", False):
                return
            key = _itchio_api_key()
            if not key:
                _itchio_set_status("Enter your itch.io API key and click Connect.")
                return
            _itchio_set_status("Connecting to itch.io…")
            _itchio_set_connecting(True)
            # Validation + collection/owned listing all run off the UI
            # thread via getit_run_in_thread; results are marshalled back
            # to the UI thread through Qt queued signals (_ok / _err).
            def _fn(_k=key):
                ok, name = zxnu_itchio.validate_key(_k)
                if not ok:
                    raise RuntimeError(name)
                cols  = zxnu_itchio.list_collections(_k)
                owned = zxnu_itchio.owned_game_ids(_k)
                try:
                    created = zxnu_itchio.created_games(_k)
                except Exception:
                    created = []      # a creator-less account is normal
                return (name, cols, owned, created)
            def _ok(res):
                _itchio_set_connecting(False)
                host._itchio_set_connected(True)
                name, cols, owned, created = res
                host._itchio_created = created
                host._itchio_collections = cols
                host._itchio_owned = owned
                # New connection → the cached search library is stale.
                host._itchio_library = None
                host.itchio_collection_combo.blockSignals(True)
                host.itchio_collection_combo.clear()
                # Purchases / owned games first, then the user's collections.
                host.itchio_collection_combo.addItem(
                    f"🛒 Purchased / Owned games ({len(owned)})",
                    _ITCHIO_OWNED_KEY)
                # The creator's own projects - drafts included, hence the
                # count fetched at connect: an empty creator account adds
                # no entry rather than a dead row.
                if created:
                    host.itchio_collection_combo.addItem(
                        f"\U0001f6e0 My projects / created ({len(created)})",
                        _ITCHIO_CREATED_KEY)
                for c in cols:
                    host.itchio_collection_combo.addItem(
                        f"{c['title']} ({c['count']})", c["id"])
                # Default to the purchased / owned games.
                host.itchio_collection_combo.setCurrentIndex(0)
                host.itchio_collection_combo.blockSignals(False)
                _itchio_set_status(
                    f"Connected as {name} — {len(cols)} collection(s), "
                    f"{len(owned)} purchased.")
                _itchio_load_selection(
                    host.itchio_collection_combo.currentData())
                # Pre-build the combined search library (collections +
                # purchases) in the background so the first Unite! search is
                # instant rather than triggering the heavy fetch on demand.
                host._itchio_prebuild_library()
                # Login succeeded — this is the moment to check itch.io for a
                # newer CSpect build (the check self-gates and runs once per
                # session; the startup timer is the no-tab fallback).
                _check_cspect = getattr(
                    host, "_check_cspect_update_async", None)
                if _check_cspect is not None:
                    try:
                        _check_cspect()
                    except Exception:
                        pass
                _check_zxnr = getattr(
                    host, "_check_zxnextremote_update_async", None)
                if _check_zxnr is not None:
                    try:
                        _check_zxnr()
                    except Exception:
                        pass
            def _err(e):
                _itchio_set_connecting(False)
                _itchio_set_status(f"itch.io: {e}")
            getit_run_in_thread(_fn, _ok, _err)
        host._itchio_load_collections = _itchio_load_collections

        def _itchio_set_connected(state):
            """Reflect the connected state on the Connect/Disconnect button."""
            host._itchio_connected = bool(state)
            try:
                # ui_tr_now, not a bare literal: this runs on every connect /
                # disconnect, long after translate_widget_tree walked the tree,
                # so an untranslated label here would stick.
                host.itchio_connect_button.setText(
                    ui_tr_now("Disconnect") if state else ui_tr_now("Connect"))
                host.itchio_connect_button.setToolTip(
                    ui_tr_now("Disconnect from itch.io and clear the listed "
                              "items.")
                    if state else
                    ui_tr_now("Connect to itch.io using the API key above."))
            except RuntimeError:
                pass
        host._itchio_set_connected = _itchio_set_connected

        def _itchio_do_connect():
            key = host.itchio_key_input.text().strip()
            configuration_dictionary[SETTING_ITCHIO_API_KEY] = key
            if not host._initialising:
                save_configuration_file()
            _itchio_load_collections()

        def _itchio_disconnect():
            """Drop the connected session and clear all listed items. The
            saved API key is kept so a single click reconnects."""
            host._itchio_collections = []
            host._itchio_owned = set()
            host._itchio_library = None
            host._itchio_last_entries = []
            try:
                host.itchio_collection_combo.blockSignals(True)
                host.itchio_collection_combo.clear()
                host.itchio_collection_combo.blockSignals(False)
            except RuntimeError:
                pass
            _itchio_populate([])   # clear both gallery and table views
            try: host._itchio_ac_model.setStringList([])
            except Exception: pass
            _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ITCHIO)
            _itchio_set_connected(False)
            _itchio_set_status("Disconnected. Click Connect to reconnect.")
            # Drop itch.io items from the Unite! aggregate too.
            _aio = getattr(host, "_allinone_repopulate", None)
            if _aio is not None:
                try: _aio()
                except Exception: pass
        host._itchio_disconnect = _itchio_disconnect

        def _itchio_toggle_connect():
            if host._itchio_connected:
                _itchio_disconnect()
            else:
                _itchio_do_connect()

        def _itchio_on_collection_changed(_idx):
            _itchio_load_selection(host.itchio_collection_combo.currentData())

        def _itchio_on_getkey():
            try:
                import webbrowser
                webbrowser.open("https://itch.io/user/settings/api-keys", new=2)
            except Exception:
                pass

        def _itchio_on_search():
            """Search the itch.io library (collections + purchases) from the
            itch.io tab. When multi-search is enabled it also fans out to the
            other source panes, mirroring their search boxes."""
            q = host.itchio_search_input.text().strip()
            if q and len(q) < SEARCH_MIN_CHARS:
                return
            try:
                host._itchio_completer.popup().hide()
            except Exception:
                pass
            if not q:
                # Empty query → restore the current collection / purchases.
                _itchio_load_selection(host.itchio_collection_combo.currentData())
                _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ITCHIO)
                return
            key = _itchio_api_key()
            if not host._itchio_connected or not key:
                _itchio_set_status("Connect to itch.io first (enter your API key).")
                return
            ql = q.lower()
            cols = list(host._itchio_collections or [])
            cached = host._itchio_library
            _itchio_set_status(f"Searching your library for “{q}”…")
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ITCHIO)
            def _fn(_k=key, _cols=cols, _lib=cached):
                lib = _lib
                if lib is None:
                    lib = zxnu_itchio.library_games(_k, _cols)
                return lib
            def _ok(lib):
                host._itchio_library = lib
                host._itchio_update_completer()
                matches = [g for g in lib
                           if ql in (g.get("title") or "").lower()
                           or ql in (g.get("author") or "").lower()]
                _itchio_populate(matches)
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ITCHIO)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ITCHIO, len(matches))
                _itchio_set_status(f"{len(matches)} result(s) for “{q}”.")
                _aio = getattr(host, "_allinone_repopulate", None)
                if _aio is not None:
                    try: _aio()
                    except Exception: pass
            def _err(e):
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ITCHIO)
                _itchio_set_status(f"itch.io: {e}")
            getit_run_in_thread(_fn, _ok, _err)
            # Multi-search fan-out to the other panes (like getit_on_search).
            if _multi_search_enabled():
                try: host.getit_search_input.setText(q)
                except Exception: pass
                if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                    try: host.zxdb_search_input.setText(q)
                    except Exception: pass
                if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                    try: host.zxart_search_input.setText(q)
                    except Exception: pass
                try:
                    _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_GETIT)
                    if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                        _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXDB)
                    if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                        _clear_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ZXART)
                except Exception:
                    pass
                try: _cross_search_getit(q)
                except Exception: pass
                if ZX_NEXT_UNITE_SHOW_ZXDB_PANE:
                    try: _cross_search_zxdb(q)
                    except Exception: pass
                if ZX_NEXT_UNITE_SHOW_ZXART_PANE:
                    try: _cross_search_zxart(q)
                    except Exception: pass

        host.itchio_connect_button.clicked.connect(_itchio_toggle_connect)
        host.itchio_key_input.returnPressed.connect(_itchio_do_connect)
        host.itchio_refresh_button.clicked.connect(_itchio_do_connect)
        host.itchio_collection_combo.currentIndexChanged.connect(
            _itchio_on_collection_changed)
        host.itchio_getkey_button.clicked.connect(_itchio_on_getkey)
        host.itchio_search_button.clicked.connect(_itchio_on_search)
        host.itchio_search_input.returnPressed.connect(_itchio_on_search)

        def _itchio_prebuild_library():
            """Fetch the combined library (collections + purchases) off the
            UI thread and cache it, so the first Unite! search is instant.
            Guarded so it never rebuilds while a build is already running."""
            key = _itchio_api_key()
            if not key or getattr(host, "_itchio_library_building", False):
                return
            host._itchio_library_building = True
            cols = list(host._itchio_collections or [])
            def _fn(_k=key, _cols=cols):
                return zxnu_itchio.library_games(_k, _cols)
            def _ok(lib):
                host._itchio_library = lib
                host._itchio_library_building = False
                host._itchio_update_completer()
                # Refresh the Unite! suggestion list now that the library
                # (and its purchased/collection titles) is available.
                _aio_notify = getattr(host, "_allinone_ac_notify", None)
                if callable(_aio_notify):
                    try: _aio_notify("itchio", "")
                    except Exception: pass
            def _err(_e):
                host._itchio_library_building = False
            getit_run_in_thread(_fn, _ok, _err)
        host._itchio_prebuild_library = _itchio_prebuild_library

        # Cross-search entry point used by the Unite! multi-search. Searches
        # the user's own library — every game across their collections plus
        # their purchases — by title/author. The combined library is built
        # once (off the UI thread) and cached until the next Connect.
        def _itchio_cross_search(query, on_done=None):
            key = _itchio_api_key()
            # Only contribute to multi-search while actively connected.
            if not host._itchio_connected or not key or not query:
                if on_done: on_done()
                return
            q = query.strip().lower()
            cols = list(host._itchio_collections or [])
            cached = host._itchio_library
            _start_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ITCHIO)
            def _fn(_k=key, _cols=cols, _lib=cached):
                lib = _lib
                if lib is None:
                    lib = zxnu_itchio.library_games(_k, _cols)
                return lib
            def _ok(lib):
                host._itchio_library = lib   # cache for subsequent searches
                host._itchio_update_completer()
                matches = [g for g in lib
                           if q in (g.get("title") or "").lower()
                           or q in (g.get("author") or "").lower()]
                # Show the searched items on the itch.io tab itself (not just
                # in the Unite! aggregate), so opening the tab after a Unite!
                # search shows the matches.
                _itchio_populate(matches)
                _itchio_set_status(f"{len(matches)} result(s) for “{query}”.")
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ITCHIO)
                _set_tab_badge(ZX_NEXT_UNITE_TAB_TITLE_ITCHIO, len(matches))
                _aio = getattr(host, "_allinone_repopulate", None)
                if _aio is not None:
                    try: _aio()
                    except Exception: pass
                if on_done: on_done()
            def _err(_e):
                _stop_tab_spinner(ZX_NEXT_UNITE_TAB_TITLE_ITCHIO)
                if on_done: on_done()
            getit_run_in_thread(_fn, _ok, _err)
        host._itchio_cross_search = _itchio_cross_search

        # --- Assemble + insert the tab (shown by default; Settings hides it) ---
        _itchio_grid.addWidget(host._itchio_stack)
        zxnextunite_itchio_tab.setLayout(_itchio_grid)
        zxnextunite_itchio_tab.tab_name_private = ZX_NEXT_UNITE_TAB_TITLE_ITCHIO
        host._itchio_tab_widget = zxnextunite_itchio_tab

        def _itchio_target_index(tabw):
            """Slot the itch.io tab right after the ZXDB tab; fall back to
            after ZXArt, then after GetIt, else the end."""
            for _title in (ZX_NEXT_UNITE_TAB_TITLE_ZXDB,
                           ZX_NEXT_UNITE_TAB_TITLE_ZXART,
                           ZX_NEXT_UNITE_TAB_TITLE_GETIT):
                for _i in range(tabw.count()):
                    if tabw.tabText(_i).startswith(_title):
                        return _i + 1
            return tabw.count()
        host._itchio_target_index = _itchio_target_index

        wid_inner.tab.insertTab(
            _itchio_target_index(wid_inner.tab),
            zxnextunite_itchio_tab, ZX_NEXT_UNITE_TAB_TITLE_ITCHIO)
        # Startup auto-connect (when a key is saved) is triggered from the
        # config-restore path, since the saved key is not loaded yet here.
