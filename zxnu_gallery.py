"""Gallery widgets for zx-next-unite: thumbnail cells, the gallery grid
view, the in-pane item viewer and the animated background widget.

Extracted from zx-next-unite.py."""

import os
import sys
import webbrowser

from zxnu_config import *
from zxnu_media import *
from PySide6.QtCore import QDir, QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFontInfo, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget, QToolButton, QVBoxLayout, QWidget


class GalleryCell(QFrame):
    """A picture-view tile: thumbnail + title + (hover-only) info line.

    The cell is fully asynchronous:
      * `thumb_fetch_cb(entry, set_pixmap, set_screenshots)` is invoked once
        when the cell is shown. The callback is responsible for resolving the
        main screenshot bytes off the UI thread and calling `set_pixmap(QPixmap)`
        on the UI thread. If extra screenshots become available it should call
        `set_screenshots([url, ...])` and the cell will then start cycling.
      * `extra_fetch_cb(url, on_pixmap)` is invoked lazily when a new url is
        about to be displayed and the pixmap is not yet cached.

    Selection is communicated via the `clicked` signal which forwards the
    entry dict. Animation honors the global animation mode ("hover"/"timer").
    """

    clicked     = Signal(object)
    dbl_clicked = Signal(object)

    _PLACEHOLDER_COLOR = QColor("#222")

    def __init__(self, entry, anim_mode_getter,
                 thumb_fetch_cb, extra_fetch_cb,
                 title_text="", info_text="", tooltip_text="",
                 context_menu_cb=None,
                 tags=None, parent=None,
                 is_favorite_cb=None, toggle_favorite_cb=None,
                 source_label_getter=None,
                 source_overlay_anchor="topleft"):
        super().__init__(parent)
        if tooltip_text:
            self.setToolTip(tooltip_text)
        self._entry = entry
        self._anim_mode_getter = anim_mode_getter  # callable -> "hover"|"timer"
        self._thumb_fetch_cb = thumb_fetch_cb
        self._extra_fetch_cb = extra_fetch_cb
        self._context_menu_cb = context_menu_cb
        self._is_favorite_cb     = is_favorite_cb
        self._toggle_favorite_cb = toggle_favorite_cb
        self._source_label_getter = source_label_getter
        self._source_overlay_anchor = source_overlay_anchor or "topleft"
        self._screenshots = []        # list of URL strings (or dicts {"url": ...})
        self._shot_cache  = {}        # url -> QPixmap
        self._shot_index  = 0
        self._hovered     = False
        self._selected    = False
        self._thumb_w     = 160       # last applied width hint
        self._tags        = [str(t) for t in (tags or []) if t]
        # Callable set by GalleryView when this cell ends up with no usable
        # image (either an explicit empty set_screenshots, or every URL in
        # the cycling list failed to load).  The view uses it to move the
        # cell to the end of the grid so the user sees items with pictures
        # first.  We call it at most once per cell.
        self._no_image_cb = None
        self._no_image_notified = False
        # Callable(cell, QPixmap, url) set by GalleryView so a resolved real
        # picture can be cached for instant redraw across an image-first
        # re-sort.  Called whenever a non-placeholder pixmap is applied.
        self._pixmap_ready_cb = None
        self._last_applied_url = ""

        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Plain)
        self.setLineWidth(1)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self._apply_style()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        self._thumb_lbl = QLabel(self)
        self._thumb_lbl.setAlignment(Qt.AlignCenter)
        self._thumb_lbl.setMinimumHeight(60)
        self._thumb_lbl.setStyleSheet("background-color: #222; color: #888;")
        self._thumb_lbl.setText("…")
        self._thumb_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.addWidget(self._thumb_lbl, 1)

        # Overlay tag badges (top-right of the thumbnail). Floats above the
        # pixmap via a child QLabel that we re-position in resizeEvent.
        self._tag_overlay = QLabel(self._thumb_lbl)
        self._tag_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._tag_overlay.setAlignment(Qt.AlignTop | Qt.AlignRight)
        self._tag_overlay.setTextFormat(Qt.RichText)
        self._tag_overlay.setStyleSheet("background: transparent;")
        self._tag_overlay.setVisible(False)
        self._refresh_tag_overlay()

        # Heart (favorite) button overlay – bottom-right of the thumbnail.
        self._heart_btn = QToolButton(self._thumb_lbl)
        self._heart_btn.setCursor(Qt.PointingHandCursor)
        self._heart_btn.setAutoRaise(True)
        self._heart_btn.setFocusPolicy(Qt.NoFocus)
        self._heart_btn.setText("♡")
        self._heart_btn.setToolTip("Add to favorites")
        self._heart_btn.setStyleSheet(
            "QToolButton { color: #ff5577; background: rgba(0,0,0,140);"
            " border: 1px solid rgba(255,255,255,40); border-radius: 10px;"
            " padding: 0px 4px; font-size: 14pt; font-weight: bold; }"
            "QToolButton:hover { background: rgba(0,0,0,200);"
            " border-color: #ff5577; }"
        )
        self._heart_btn.setVisible(bool(self._toggle_favorite_cb))
        self._heart_btn.clicked.connect(self._on_heart_clicked)

        # Source-pane overlay badge (top-left of thumbnail).  Used by the
        # Favorites gallery to show whether an item came from GetIt / ZXDB /
        # zxArt.  Hidden by default for the regular galleries.
        self._source_overlay = QLabel(self._thumb_lbl)
        self._source_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._source_overlay.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._source_overlay.setTextFormat(Qt.RichText)
        self._source_overlay.setStyleSheet("background: transparent;")
        self._source_overlay.setVisible(False)
        self._refresh_source_overlay()
        self._refresh_heart()

        self._title_lbl = QLabel(title_text or "", self)
        self._title_lbl.setAlignment(Qt.AlignCenter)
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setTextFormat(Qt.RichText)
        f = self._title_lbl.font()
        f.setBold(True)
        self._title_lbl.setFont(f)
        lay.addWidget(self._title_lbl, 0)

        self._info_lbl = QLabel(info_text or "", self)
        self._info_lbl.setAlignment(Qt.AlignCenter)
        self._info_lbl.setWordWrap(True)
        self._info_lbl.setStyleSheet("color: #aaa;")
        self._info_lbl.setVisible(False)
        lay.addWidget(self._info_lbl, 0)

        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._advance)

        # Retry bookkeeping for the initial thumbnail fetch.  The per-gallery
        # fetch callbacks run on background threads whose error path is a no-op,
        # so a transient network/JSON failure leaves the cell stuck on the "…"
        # placeholder forever (until the user opens the item full-screen, which
        # kicks off a fresh fetch).  We retry the initial fetch a few times with
        # back-off whenever the cell has neither loaded a real picture nor
        # resolved to a typed placeholder.
        self._loaded_ok          = False   # a non-placeholder pixmap was shown
        self._placeholder_shown  = False   # a "no real picture" placeholder shown
        self._fetch_attempts     = 0
        self._max_fetch_attempts = 4
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._retry_if_unloaded)

        # Defer initial fetch until the cell is actually shown
        QTimer.singleShot(0, self._kickoff_initial_fetch)

    # ---- public API ----------------------------------------------------

    def entry(self):
        return self._entry

    def set_selected(self, selected: bool):
        if self._selected == selected:
            return
        self._selected = selected
        self._apply_style()

    def set_thumb_width(self, w: int):
        """Called by the parent gallery view when the column width changes."""
        w = max(40, int(w))
        changed = abs(w - self._thumb_w) >= 4
        self._thumb_w = w
        # Always rescale the current pixmap. Even when the requested width is
        # unchanged, the pixmap may have been left at the wrong size after
        # returning from a fullscreen / in-pane viewer where the underlying
        # QLabel briefly reported a much larger geometry.
        cur = self._current_pixmap()
        if cur is not None:
            self._apply_pixmap(cur)
        return changed

    def set_tags(self, tags):
        """Replace the overlay tag badges."""
        self._tags = [str(t) for t in (tags or []) if t]
        self._refresh_tag_overlay()

    def set_info_text(self, text: str):
        """Update the hover-only info line (author / date / category, …).

        Called asynchronously by thumb-fetch callbacks once richer metadata
        becomes available (e.g. GetIt detail records that include the date).
        """
        try:
            self._info_lbl.setText(text or "")
        except Exception:
            pass

    def _refresh_tag_overlay(self):
        if not getattr(self, "_tag_overlay", None):
            return
        if not self._tags:
            self._tag_overlay.setVisible(False)
            self._tag_overlay.setText("")
            return
        # Build a row of HTML chips. Inline-block keeps them right-aligned and
        # wrapping naturally on narrow tiles.
        chip_css = (
            "background:#1c3a52;color:#bfe6ff;border:1px solid #2f6f9a;"
            "border-radius:3px;padding:1px 5px;margin:1px 2px;"
            "font-size:9pt;font-weight:600;"
        )
        chips = "&nbsp;".join(
            f"<span style='{chip_css}'>{t}</span>"
            for t in self._tags
        )
        html = f"<div style='text-align:right;'>{chips}</div>"
        self._tag_overlay.setText(html)
        self._tag_overlay.setVisible(True)
        self._position_tag_overlay()

    def _position_tag_overlay(self):
        ov = getattr(self, "_tag_overlay", None)
        if ov is None or not ov.isVisible():
            return
        # Anchor to top-right with a small padding inside the thumb label.
        pad = 4
        max_w = max(40, self._thumb_lbl.width() - 2 * pad)
        ov.adjustSize()
        w = min(ov.width(), max_w)
        h = ov.sizeHint().height()
        ov.setGeometry(self._thumb_lbl.width() - w - pad, pad, w, h)
        ov.raise_()

    def _refresh_source_overlay(self):
        ov = getattr(self, "_source_overlay", None)
        if ov is None:
            return
        getter = self._source_label_getter
        label = ""
        if getter is not None:
            try:
                label = str(getter(self._entry) or "").strip()
            except Exception:
                label = ""
        if not label:
            ov.setVisible(False)
            ov.setText("")
            return
        chip_css = (
            "background:#3a1c52;color:#e8bfff;border:1px solid #6f2f9a;"
            "border-radius:3px;padding:1px 6px;margin:1px 2px;"
            "font-size:9pt;font-weight:600;"
        )
        ov.setText(f"<div style='text-align:left;'>"
                   f"<span style='{chip_css}'>{label}</span></div>")
        ov.setVisible(True)
        self._position_source_overlay()

    def _position_source_overlay(self):
        ov = getattr(self, "_source_overlay", None)
        if ov is None or not ov.isVisible():
            return
        pad = 4
        ov.adjustSize()
        w = min(ov.width(), max(40, self._thumb_lbl.width() - 2 * pad))
        h = ov.sizeHint().height()
        anchor = getattr(self, "_source_overlay_anchor", "topleft")
        if anchor == "bottomleft":
            x = pad
            y = self._thumb_lbl.height() - h - pad
        elif anchor == "bottomright":
            # Sit to the left of the heart button (which is anchored bottom-
            # right) if one is shown, otherwise hug the right edge.
            heart = getattr(self, "_heart_btn", None)
            heart_w = 0
            if heart is not None and heart.isVisible():
                heart_w = heart.sizeHint().width() + pad
            x = max(pad, self._thumb_lbl.width() - w - pad - heart_w)
            y = self._thumb_lbl.height() - h - pad
        else:
            x = pad
            y = pad
        ov.setGeometry(x, y, w, h)
        ov.raise_()

    def _refresh_heart(self):
        btn = getattr(self, "_heart_btn", None)
        if btn is None:
            return
        if not self._toggle_favorite_cb:
            btn.setVisible(False)
            return
        is_fav = False
        if self._is_favorite_cb is not None:
            try:
                is_fav = bool(self._is_favorite_cb(self._entry))
            except Exception:
                is_fav = False
        btn.setText("♥" if is_fav else "♡")
        btn.setToolTip("Remove from favorites" if is_fav else "Add to favorites")
        btn.setVisible(True)
        self._position_heart()

    def _position_heart(self):
        btn = getattr(self, "_heart_btn", None)
        if btn is None or not btn.isVisible():
            return
        pad = 4
        btn.adjustSize()
        w = btn.sizeHint().width()
        h = btn.sizeHint().height()
        btn.setGeometry(self._thumb_lbl.width() - w - pad,
                         self._thumb_lbl.height() - h - pad, w, h)
        btn.raise_()

    def _on_heart_clicked(self):
        if not self._toggle_favorite_cb:
            return
        try:
            self._toggle_favorite_cb(self._entry)
        except Exception:
            pass
        self._refresh_heart()

    def set_favorite_state_changed(self):
        """External hook: re-query the favorite predicate (e.g. when another
        view toggled the favorite state for this entry)."""
        self._refresh_heart()

    def _is_alive(self) -> bool:
        """Return False if the underlying C++ QWidget has already been deleted
        (which happens when GalleryView.populate() tears cells down while
        background thumbnail fetches are still in flight)."""
        try:
            # Touching any property forces shiboken to validate the C++ object.
            self.objectName()
            return True
        except RuntimeError:
            return False

    def set_screenshots(self, urls):
        """Replace the list of cycle-able image URLs. The first entry is also
        the main thumbnail (used immediately if already cached)."""
        if not self._is_alive():
            return
        urls = [u for u in (urls or []) if u]
        self._screenshots = urls
        if urls and urls[0] in self._shot_cache:
            self._apply_pixmap(self._shot_cache[urls[0]])
        self._maybe_start_anim()
        # Proactively validate every URL so broken ones (HTTP 404, network
        # error, etc.) are dropped from the cycling list up front rather
        # than producing a black frame when the timer lands on them.
        self._prefetch_all()
        # An entry made of nothing but synthetic placeholder URLs has no
        # real picture: notify the view so it can move the cell to the end
        # of the grid (the user wants image-bearing items first).
        if not urls:
            self._notify_no_image()
        elif all(isinstance(u, str) and u.startswith("placeholder://")
                 for u in urls):
            self._notify_no_image()

    def _prefetch_all(self):
        if not self._extra_fetch_cb or not self._is_alive():
            return
        for url in list(self._screenshots):
            if url in self._shot_cache:
                continue
            def _on_px(pm, _u=url):
                if not self._is_alive():
                    return
                if pm is None or pm.isNull():
                    self._drop_bad_url(_u)
                    return
                self._shot_cache[_u] = pm
                if self._shot_index < len(self._screenshots) and self._screenshots[self._shot_index] == _u:
                    self._apply_pixmap(pm)
            try:
                self._extra_fetch_cb(url, _on_px)
            except Exception:
                self._drop_bad_url(url)

    def set_main_pixmap(self, pm: QPixmap, url: str = ""):
        if pm is None or pm.isNull():
            return
        if not self._is_alive():
            return
        if url:
            self._shot_cache[url] = pm
            if not self._screenshots:
                self._screenshots = [url]
        if self._shot_index < len(self._screenshots):
            cur_url = self._screenshots[self._shot_index]
            if not url or cur_url == url:
                self._apply_pixmap(pm)

    # ---- events --------------------------------------------------------

    def enterEvent(self, ev):
        self._hovered = True
        self._info_lbl.setVisible(True)
        if self._anim_mode_getter() == "hover":
            # advance to next frame eagerly on hover, then keep cycling
            if len(self._screenshots) > 1:
                self._timer.start()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hovered = False
        self._info_lbl.setVisible(False)
        if self._anim_mode_getter() == "hover":
            self._timer.stop()
            if self._screenshots:
                self._shot_index = 0
                self._show_index(0)
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self._entry)
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.dbl_clicked.emit(self._entry)
        super().mouseDoubleClickEvent(ev)

    def contextMenuEvent(self, ev):
        if self._context_menu_cb and self._is_alive():
            self._context_menu_cb(self._entry, ev.globalPos())
            ev.accept()
        else:
            super().contextMenuEvent(ev)

    def resizeEvent(self, ev):
        # Re-scale the currently shown pixmap to the new width.
        cur = self._current_pixmap()
        if cur is not None:
            self._apply_pixmap(cur)
        self._position_tag_overlay()
        self._position_source_overlay()
        self._position_heart()
        super().resizeEvent(ev)

    # ---- internals -----------------------------------------------------

    def _apply_style(self):
        border = "#4fc3f7" if self._selected else "#555"
        bg = "#1a2733" if self._selected else "#181818"
        self.setStyleSheet(
            f"GalleryCell {{ background-color: {bg}; border: 1px solid {border}; border-radius: 4px; }}"
        )

    def _kickoff_initial_fetch(self):
        if not self._thumb_fetch_cb:
            return
        if not self._is_alive():
            return
        self._fetch_attempts += 1
        try:
            # Newer callbacks accept an extra `set_tags` callable so that
            # tags can be derived asynchronously (e.g. from a release lookup).
            try:
                self._thumb_fetch_cb(self._entry, self.set_main_pixmap,
                                     self.set_screenshots, self.set_tags,
                                     self.set_info_text)
            except TypeError:
                try:
                    self._thumb_fetch_cb(self._entry, self.set_main_pixmap,
                                         self.set_screenshots, self.set_tags)
                except TypeError:
                    self._thumb_fetch_cb(self._entry, self.set_main_pixmap,
                                         self.set_screenshots)
        except Exception:
            pass
        # Schedule a retry in case this attempt fails silently (the per-gallery
        # fetch callbacks swallow background errors), leaving the cell stuck on
        # the "…" placeholder.  The retry is a no-op once a real picture or a
        # typed placeholder has been shown.
        self._schedule_retry()

    def _schedule_retry(self):
        if not self._is_alive():
            return
        if self._loaded_ok or self._placeholder_shown:
            return
        if self._fetch_attempts >= self._max_fetch_attempts:
            return
        # Exponential-ish back-off: 2s, 4s, 8s …
        delay_ms = 2000 * (2 ** (self._fetch_attempts - 1))
        try:
            self._retry_timer.start(delay_ms)
        except Exception:
            pass

    def _retry_if_unloaded(self):
        if not self._is_alive():
            return
        if self._loaded_ok or self._placeholder_shown:
            return
        self._kickoff_initial_fetch()

    def _maybe_start_anim(self):
        if len(self._screenshots) <= 1:
            self._timer.stop()
            return
        mode = self._anim_mode_getter()
        if mode == "timer":
            self._timer.start()
        elif mode == "hover" and self._hovered:
            self._timer.start()

    def _advance(self):
        if not self._screenshots:
            return
        nxt = (self._shot_index + 1) % len(self._screenshots)
        self._shot_index = nxt
        self._show_index(nxt)

    def _show_index(self, idx: int):
        if not self._screenshots:
            return
        url = self._screenshots[idx]
        cached = self._shot_cache.get(url)
        if cached is not None:
            self._apply_pixmap(cached)
            return
        if self._extra_fetch_cb:
            def _on_px(pm, _u=url):
                if pm is None or pm.isNull():
                    # Drop the broken URL so it never causes a black frame
                    # in the cycling thumbnail.
                    self._drop_bad_url(_u)
                    return
                self._shot_cache[_u] = pm
                if self._shot_index < len(self._screenshots) and self._screenshots[self._shot_index] == _u:
                    self._apply_pixmap(pm)
            try:
                self._extra_fetch_cb(url, _on_px)
            except Exception:
                self._drop_bad_url(url)

    def _drop_bad_url(self, url: str):
        """Remove a URL that failed to load from the cycling list and refresh
        the displayed frame if necessary."""
        if not self._is_alive():
            return
        try:
            i = self._screenshots.index(url)
        except ValueError:
            return
        del self._screenshots[i]
        self._shot_cache.pop(url, None)
        if not self._screenshots:
            self._timer.stop()
            self._shot_index = 0
            self._notify_no_image()
            return
        new_idx = self._shot_index
        if i < self._shot_index:
            new_idx = self._shot_index - 1
        if new_idx >= len(self._screenshots):
            new_idx = 0
        self._shot_index = new_idx
        if len(self._screenshots) <= 1:
            self._timer.stop()
        # Show whichever URL is now at this slot (cached or trigger a fetch).
        self._show_index(new_idx)

    def _notify_no_image(self):
        if self._no_image_notified:
            return
        self._no_image_notified = True
        cb = self._no_image_cb
        if cb is None:
            return
        try:
            cb(self)
        except Exception:
            pass

    def _current_pixmap(self):
        if not self._screenshots:
            return None
        if self._shot_index >= len(self._screenshots):
            return None
        return self._shot_cache.get(self._screenshots[self._shot_index])

    def _apply_pixmap(self, pm: QPixmap):
        if pm is None or pm.isNull():
            return
        if not self._is_alive():
            return
        # The pixmap width is driven exclusively by the value last requested
        # by the parent GalleryView (set_thumb_width).  We deliberately ignore
        # QLabel.width() here: live geometry can momentarily report a much
        # larger value during a style polish (e.g. after a click toggles the
        # selection stylesheet) or after returning from a fullscreen viewer,
        # which would otherwise scale every cell's pixmap to a giant size and
        # visibly break the 4-column gallery row.
        target_w = max(40, int(self._thumb_w))
        # Reserve a 4:3 area, but let the pixmap aspect ratio decide
        scaled = pm.scaled(target_w, int(target_w * 3 / 4),
                           Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._thumb_lbl.setPixmap(scaled)
        self._thumb_lbl.setText("")
        self._position_tag_overlay()
        self._position_source_overlay()
        self._position_heart()
        # A pixmap was successfully applied: mark the cell loaded so the
        # initial-fetch retry timer stops re-trying.  A synthetic placeholder
        # counts as "resolved" too (we have nothing better to show), but it is
        # tracked separately so a real picture is still preferred.
        cur_url = ""
        if 0 <= self._shot_index < len(self._screenshots):
            cur_url = self._screenshots[self._shot_index]
        is_placeholder = isinstance(cur_url, str) and cur_url.startswith("placeholder://")
        if is_placeholder:
            self._placeholder_shown = True
        else:
            self._loaded_ok = True
        try:
            self._retry_timer.stop()
        except Exception:
            pass
        # Surface the resolved real picture to the parent view so it can be
        # cached for an instant redraw across an image-first re-sort.
        cb = self._pixmap_ready_cb
        if cb is not None:
            if not is_placeholder:
                try:
                    cb(self, pm, cur_url)
                except Exception:
                    pass


class GalleryView(QWidget):
    """4-column thumbnail grid wrapper around a QTableWidget.

    The view does NOT own pagination — the surrounding pane drives populate()
    per page. The grid resizes columns equally; cell widgets are GalleryCell.
    """

    cell_clicked     = Signal(object)
    cell_dbl_clicked = Signal(object)

    def __init__(self, rows_per_page_getter, anim_mode_getter,
                 thumb_fetch_cb, extra_fetch_cb,
                 title_getter, info_getter, context_menu_cb=None,
                 tags_getter=None, image_predicate=None,
                 no_image_key_getter=None,
                 is_favorite_cb=None, toggle_favorite_cb=None,
                 source_label_getter=None, tooltip_getter=None,
                 cols_getter=None, img_size_getter=None, parent=None,
                 source_overlay_anchor="topleft"):
        super().__init__(parent)
        self._rows_per_page_getter = rows_per_page_getter
        self._anim_mode_getter     = anim_mode_getter
        self._cols_getter          = cols_getter
        self._img_size_getter      = img_size_getter
        self._thumb_fetch_cb       = thumb_fetch_cb
        self._extra_fetch_cb       = extra_fetch_cb
        self._title_getter         = title_getter
        self._info_getter          = info_getter
        self._tooltip_getter       = tooltip_getter
        self._context_menu_cb      = context_menu_cb
        self._tags_getter          = tags_getter or _gallery_extract_tags
        self._is_favorite_cb       = is_favorite_cb
        self._toggle_favorite_cb   = toggle_favorite_cb
        self._source_label_getter  = source_label_getter
        self._source_overlay_anchor = source_overlay_anchor or "topleft"
        # Optional predicate(entry) -> bool returning True when the entry
        # is known to have at least one image at populate-time.  Entries
        # for which the predicate returns False are moved to the end of
        # the grid so the user sees items with images first.
        self._image_predicate      = image_predicate
        # Optional callable(entry) -> hashable identity used to remember,
        # across re-populates within the same page, which entries turned
        # out to have *no* real picture (only a synthetic placeholder).
        # As background fetches resolve, imageless entries are learned and
        # the grid is re-sorted (image-bearing first) on a short debounce.
        self._no_image_key_getter  = no_image_key_getter
        self._no_image_keys        = set()
        self._current_entries      = []
        # Shared pixmap cache (entry-key -> (QPixmap, url)) so an image-first
        # re-sort can redraw resolved pictures instantly without re-fetching.
        self._pixmap_cache         = {}
        self._resort_timer = QTimer(self)
        self._resort_timer.setSingleShot(True)
        self._resort_timer.setInterval(250)
        self._resort_timer.timeout.connect(self._resort_for_images)
        self._cells = []
        self._selected_cell = None

        # Let the transparent window background (BackgroundWidget) show through
        # any empty area of the grid.  The populated cells keep their own
        # opaque GalleryCell background, so loaded items are NOT transparent;
        # only the gaps / trailing empty cells reveal the background image.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("GalleryView { background: transparent; }")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._table = QTableWidget(0, self._cols(), self)
        self._table.horizontalHeader().setVisible(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setFocusPolicy(Qt.NoFocus)
        self._table.setShowGrid(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        # Make the table and its viewport transparent so empty cells reveal
        # the background image instead of an opaque table background.
        self._table.setAttribute(Qt.WA_TranslucentBackground, True)
        self._table.viewport().setAttribute(Qt.WA_TranslucentBackground, True)
        self._table.viewport().setAutoFillBackground(False)
        self._table.setFrameShape(QFrame.NoFrame)
        self._table.setStyleSheet(
            "QTableWidget { background: transparent; border: none; }"
            "QTableWidget::viewport { background: transparent; }"
        )
        lay.addWidget(self._table)

        self._table.viewport().installEventFilter(self)

    def page_size(self) -> int:
        try:
            n = int(self._rows_per_page_getter())
        except Exception:
            n = DEFAULT_GALLERY_ROWS_PER_PAGE
        n = max(GALLERY_MIN_ROWS, min(GALLERY_MAX_ROWS, n))
        return n * self._cols()

    def _cols(self) -> int:
        """Return the current number of gallery columns."""
        if self._cols_getter is not None:
            try:
                return int(self._cols_getter())
            except Exception:
                pass
        return DEFAULT_GALLERY_COLS

    def _row_h(self, col_w: int) -> int:
        """Return row height for *col_w* scaled by the current image-size setting."""
        size = ""
        if self._img_size_getter is not None:
            try:
                size = str(self._img_size_getter()).lower()
            except Exception:
                pass
        if size == "small":
            return int(col_w * 3 / 8) + 40
        if size == "large":
            return int(col_w * 3 / 2) + 72
        return int(col_w * 3 / 4) + 56  # medium (default)

    def _entry_image_key(self, e):
        """Return a hashable identity for *e* used to track imageless entries
        and cache resolved pixmaps.

        When no explicit key getter is supplied we fall back to the Python
        object identity of the entry dict.  That is stable for the lifetime of
        a populated page (the same dict instances are reused when the grid is
        re-sorted) and the per-page caches are reset on every fresh
        ``populate``, so stale identities can never leak across pages."""
        if self._no_image_key_getter is not None:
            try:
                k = self._no_image_key_getter(e)
                if k is not None:
                    return k
            except Exception:
                pass
        try:
            return id(e)
        except Exception:
            return None

    def _has_image(self, e) -> bool:
        """Best-effort 'does this entry have a real picture' decision.

        Combines the populate-time predicate (when supplied) with what we
        have *learned* at runtime: an entry whose key is in the no-image set
        is treated as imageless regardless of the predicate."""
        key = self._entry_image_key(e)
        if key is not None and key in self._no_image_keys:
            return False
        if self._image_predicate is not None:
            try:
                return bool(self._image_predicate(e))
            except Exception:
                return True
        return True

    def _order_image_first(self, entries):
        """Stable-partition *entries* so items with a real picture come first
        and known-imageless items (placeholder bitmap/file/text/utility) sink
        to the bottom."""
        if not entries:
            return entries
        if self._image_predicate is None and not self._no_image_keys:
            return entries
        with_img    = [e for e in entries if self._has_image(e)]
        without_img = [e for e in entries if not self._has_image(e)]
        return with_img + without_img

    def populate(self, entries):
        """Render `entries` (list of dicts) into the grid. Excess capacity is
        cleared. Caller must have already paged the entries appropriately."""
        entries = list(entries or [])
        # A fresh page of entries: forget what we learned about the previous
        # page so the new content is evaluated from scratch.
        self._no_image_keys = set()
        self._pixmap_cache = {}
        self._resort_timer.stop()
        entries = self._order_image_first(entries)
        self._current_entries = list(entries)
        self._render_entries(entries)

    def _render_entries(self, entries):
        """(Re)build the grid widgets for *entries* in the given order.  Used
        by ``populate`` for a fresh page and by ``_resort_for_images`` when the
        image-first ordering changes at runtime."""
        entries = list(entries or [])
        rows_needed = (len(entries) + self._cols() - 1) // self._cols() if entries else 0
        # Tear down any existing cells
        for c in self._cells:
            try:
                c.setParent(None)
                c.deleteLater()
            except Exception:
                pass
        self._cells = []
        self._selected_cell = None
        self._table.setColumnCount(self._cols())
        self._table.clearContents()
        self._table.setRowCount(rows_needed)

        # Compute a sane row height: thumbnail size driven by image-size setting.
        # We re-apply this on resize as well.
        vp_w = max(200, self._table.viewport().width())
        col_w = max(80, vp_w // self._cols() - 6)
        row_h = self._row_h(col_w)
        for r in range(rows_needed):
            self._table.setRowHeight(r, row_h)
        # Ensure the table is always tall enough to show 4 rows without scrolling.
        self._table.setMinimumHeight(row_h * 4)

        for i, e in enumerate(entries):
            r, c = divmod(i, self._cols())
            title   = self._title_getter(e) if self._title_getter   else ""
            info    = self._info_getter(e)  if self._info_getter    else ""
            tooltip = self._tooltip_getter(e) if self._tooltip_getter else ""
            tags  = []
            if self._tags_getter:
                try:
                    tags = list(self._tags_getter(e) or [])
                except Exception:
                    tags = []
            cell = GalleryCell(
                entry=e,
                anim_mode_getter=self._anim_mode_getter,
                thumb_fetch_cb=self._thumb_fetch_cb,
                extra_fetch_cb=self._extra_fetch_cb,
                title_text=title,
                info_text=info,
                tooltip_text=tooltip,
                context_menu_cb=self._context_menu_cb,
                tags=tags,
                parent=self._table,
                is_favorite_cb=self._is_favorite_cb,
                toggle_favorite_cb=self._toggle_favorite_cb,
                source_label_getter=self._source_label_getter,
                source_overlay_anchor=self._source_overlay_anchor,
            )
            cell.set_thumb_width(col_w)
            cell.clicked.connect(self._on_cell_clicked)
            cell.dbl_clicked.connect(self._on_cell_dbl_clicked)
            cell._no_image_cb = self._on_cell_no_image
            cell._pixmap_ready_cb = self._on_cell_pixmap_ready
            # Seed from the shared pixmap cache so a cell that already resolved
            # its picture before a re-sort redraws instantly (no re-download,
            # no flicker) instead of falling back to the "…" placeholder.
            key = self._entry_image_key(e)
            if key is not None:
                cached = self._pixmap_cache.get(key)
                if cached is not None:
                    cell.set_main_pixmap(cached[0], cached[1])
            self._table.setCellWidget(r, c, cell)
            self._cells.append(cell)

    def _on_cell_pixmap_ready(self, cell, pm, url):
        """Cache a cell's resolved picture (keyed by entry identity) so a later
        image-first re-sort can redraw it instantly without re-fetching."""
        try:
            if pm is None or pm.isNull():
                return
            entry = cell.entry()
        except Exception:
            return
        key = self._entry_image_key(entry)
        if key is None:
            return
        if isinstance(url, str) and url.startswith("placeholder://"):
            return
        self._pixmap_cache[key] = (pm, url or "")

    def select_entry(self, predicate):
        """Mark the first cell whose entry satisfies `predicate(entry)` as
        selected. Pass `lambda e: False` to clear."""
        for cell in self._cells:
            sel = False
            try:
                sel = bool(predicate(cell.entry()))
            except Exception:
                sel = False
            cell.set_selected(sel)
            if sel:
                self._selected_cell = cell

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize and obj is self._table.viewport():
            self._apply_dimensions()
        return super().eventFilter(obj, ev)

    def refresh_favorites(self):
        """Re-query the favorite predicate on every cell. Called when the
        favorite state changes externally (e.g. via the fullscreen viewer
        or another pane's favorite toggle)."""
        for cell in self._cells:
            try:
                cell.set_favorite_state_changed()
            except Exception:
                pass

    def showEvent(self, ev):
        # When the gallery becomes visible again — for example after the user
        # closes the in-pane fullscreen viewer that lives in the same
        # QStackedWidget — re-apply dimensions so every cell rescales its
        # thumbnail to the current column width.  Without this, a cell's
        # QPixmap can remain at the size it was rendered at right before the
        # viewer was shown and visibly overflow the 4-column row.
        super().showEvent(ev)
        QTimer.singleShot(0, self._apply_dimensions)

    def _apply_dimensions(self):
        cols  = self._cols()
        if self._table.columnCount() != cols:
            self._table.setColumnCount(cols)
        vp_w = max(200, self._table.viewport().width())
        col_w = max(80, vp_w // cols - 6)
        row_h = self._row_h(col_w)
        for r in range(self._table.rowCount()):
            self._table.setRowHeight(r, row_h)
        for cell in self._cells:
            cell.set_thumb_width(col_w)
        # Keep the table tall enough to show 4 full rows without scrolling.
        self._table.setMinimumHeight(row_h * 4)

    def _on_cell_clicked(self, entry):
        sender = self.sender()
        for cell in self._cells:
            cell.set_selected(cell is sender)
        self._selected_cell = sender
        self.cell_clicked.emit(entry)

    def _on_cell_dbl_clicked(self, entry):
        self.cell_dbl_clicked.emit(entry)

    def _on_cell_no_image(self, cell):
        """Called by a GalleryCell when it has determined it has no usable
        image (an explicit empty screenshot list, only placeholder URLs, or
        every real URL failed to load).

        We remember the entry's identity in ``self._no_image_keys`` and arm a
        short debounce timer.  When it fires, the whole page is re-sorted
        (image-bearing first) via a full re-populate — the only safe way to
        reorder ``QTableWidget`` cell widgets without Qt deleting them.  The
        debounce coalesces the burst of callbacks that arrive as a page's
        fetches resolve into a single relayout, and the shared pixmap cache
        means image-bearing cells redraw instantly with no re-download."""
        try:
            entry = cell.entry()
        except Exception:
            entry = None
        if entry is None:
            return
        key = self._entry_image_key(entry)
        if key is None:
            return
        if key in self._no_image_keys:
            return
        self._no_image_keys.add(key)
        # Only bother re-sorting if this imageless cell is not already trailing
        # the grid (i.e. there is at least one later cell that *does* have an
        # image and should move ahead of it).
        self._resort_timer.start()

    def _resort_for_images(self):
        """Re-sort the current page so image-bearing entries lead, then
        re-populate.  No-op when the order would not change."""
        if not self._current_entries:
            return
        reordered = self._order_image_first(self._current_entries)
        if [id(e) for e in reordered] == [id(e) for e in self._current_entries]:
            return
        # Preserve the current selection across the relayout.
        sel_entry = None
        if self._selected_cell is not None:
            try:
                sel_entry = self._selected_cell.entry()
            except Exception:
                sel_entry = None
        self._render_entries(reordered)
        self._current_entries = list(reordered)
        if sel_entry is not None:
            self.select_entry(lambda e: e is sel_entry)

    def _apply_no_image_relayout(self):
        # Retained for backwards compatibility; routes to the debounced
        # image-first re-sort.
        self._resort_timer.start()

    def _relayout_cells(self):
        """Re-bind every cell in self._cells to its new (row, col) slot."""
        cols = self._cols()
        rows_needed = (len(self._cells) + cols - 1) // cols if self._cells else 0
        # Detach existing cells from their current slots without deleting
        # them.  setCellWidget(r,c,None) would delete the previously set
        # widget, so we use removeCellWidget which only detaches.
        total_rows = self._table.rowCount()
        for r in range(total_rows):
            for c in range(self._table.columnCount()):
                if self._table.cellWidget(r, c) is not None:
                    self._table.removeCellWidget(r, c)
        self._table.setColumnCount(cols)
        self._table.setRowCount(rows_needed)
        # Re-apply current row height to all rows.
        vp_w = max(200, self._table.viewport().width())
        col_w = max(80, vp_w // cols - 6)
        row_h = self._row_h(col_w)
        for r in range(rows_needed):
            self._table.setRowHeight(r, row_h)
        for i, cell in enumerate(self._cells):
            r, c = divmod(i, cols)
            self._table.setCellWidget(r, c, cell)


def _gallery_stars(rating_value) -> str:
    """Return a 5-star unicode string for a 0–10 or 0–5 rating value."""
    try:
        v = float(rating_value)
    except (TypeError, ValueError):
        return ""
    # Normalise: values > 5 are assumed to be on a 0–10 scale
    if v > 5:
        v = v / 2.0
    v = max(0.0, min(5.0, v))
    full  = int(v + 0.5)
    empty = 5 - full
    return "★" * full + "☆" * empty + f"  ({rating_value})"


class GalleryItemViewer(QWidget):
    """In-pane item viewer opened from Gallery mode (double-click on a cell).

    Displayed inside the pane's QStackedWidget (index 2) rather than as a
    separate OS window, so it fills the same area as the normal pane view.

    Layout
    ------
    Left (stretch 3)  – large screenshot, auto-cycling slideshow, ◀/▶ nav
    Right (fixed 340) – scrollable metadata (title, key/value rows, star
                        rating) + action bar (Download / Send to SD / NextSync)

    Close: ✕ button (top-right) or Escape key → pops stack back to index 0.
    """

    _BTN_STYLE = (
        "QPushButton { color: #eee; background: #2a2a2a; border: 1px solid #444;"
        " border-radius: 4px; padding: 6px 12px; text-align: left; }"
        "QPushButton:hover { background: #3a3a3a; border-color: #666; }"
        "QPushButton:disabled { color: #555; background: #1a1a1a; border-color: #333; }"
    )

    def __init__(self, title: str, info_rows: list, screenshots: list,
                 extra_fetch_cb, tags=None, parent=None):
        """
        Parameters
        ----------
        title          : program name shown at top of metadata panel
        info_rows      : list of (label, value) tuples; empty value → skip row
        screenshots    : list of image URL strings for the slideshow
        extra_fetch_cb : callable(url, on_pixmap_cb) – async image loader
        """
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background: #0d0d0d;")

        self._screenshots    = list(screenshots or [])
        self._shot_index     = 0
        self._shot_cache     = {}
        self._extra_fetch_cb = extra_fetch_cb
        self._close_fn       = None   # set by install_into_stack()
        self._alien_overlay  = None   # optional Alien Floyd's animation overlay
        self._tags           = [str(t) for t in (tags or []) if t]
        self._is_favorite_cb     = None
        self._toggle_favorite_cb = None
        self._fav_entry          = None
        self._title              = title or ""
        self._placeholder_label    = ""
        self._placeholder_subtitle = ""
        self._cspect_enabled       = False
        self._mame_enabled         = False

        # ── root layout ──────────────────────────────────────────────────
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── LEFT: image area ─────────────────────────────────────────────
        img_panel = QWidget()
        img_panel.setStyleSheet("background: #0a0a0a;")
        img_layout = QVBoxLayout(img_panel)
        img_layout.setContentsMargins(8, 8, 8, 8)
        img_layout.setSpacing(4)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignCenter)
        self._img_lbl.setStyleSheet("background: #0a0a0a; color: #666;")
        self._img_lbl.setText("Loading…")
        self._img_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._img_lbl.setCursor(Qt.PointingHandCursor)
        img_layout.addWidget(self._img_lbl, 1)

        # Tag overlay floating in the top-right corner of the image area.
        self._tag_overlay = QLabel(self._img_lbl)
        self._tag_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._tag_overlay.setAlignment(Qt.AlignTop | Qt.AlignRight)
        self._tag_overlay.setTextFormat(Qt.RichText)
        self._tag_overlay.setStyleSheet("background: transparent;")
        self._tag_overlay.setVisible(False)
        self._img_lbl.installEventFilter(self)
        self._refresh_tag_overlay()

        # ◀ counter ▶
        nav_row = QHBoxLayout()
        _nav_ss = (
            "QToolButton { color: white; background: #2b2b2b; border: none;"
            " font-size: 20px; padding: 5px 14px; }"
            "QToolButton:hover { background: #484848; }"
        )
        self._prev_btn = QToolButton()
        self._prev_btn.setText("◀")
        self._prev_btn.setStyleSheet(_nav_ss)
        self._next_btn = QToolButton()
        self._next_btn.setText("▶")
        self._next_btn.setStyleSheet(_nav_ss)
        self._shot_counter = QLabel("")
        self._shot_counter.setAlignment(Qt.AlignCenter)
        self._shot_counter.setStyleSheet("color: #777; font-size: 11px; min-width: 60px;")
        nav_row.addStretch()
        nav_row.addWidget(self._prev_btn)
        nav_row.addWidget(self._shot_counter)
        nav_row.addWidget(self._next_btn)
        nav_row.addStretch()
        img_layout.addLayout(nav_row)

        self._prev_btn.clicked.connect(self._go_prev)
        self._next_btn.clicked.connect(self._go_next)

        root.addWidget(img_panel, 3)

        # ── RIGHT: metadata + actions ────────────────────────────────────
        right_panel = QWidget()
        right_panel.setStyleSheet("background: #111;")
        right_panel.setMinimumWidth(300)
        right_panel.setMaximumWidth(400)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # ── close bar ────────────────────────────────────────────────────
        close_bar = QHBoxLayout()
        close_bar.setContentsMargins(10, 8, 10, 4)
        close_bar.addStretch()
        self._heart_btn = QToolButton()
        self._heart_btn.setText("♡")
        self._heart_btn.setCursor(Qt.PointingHandCursor)
        self._heart_btn.setStyleSheet(
            "QToolButton { color: #ff5577; background: #2b2b2b; border: none;"
            " font-size: 17px; padding: 3px 10px; }"
            "QToolButton:hover { background: #4a2a35; }"
        )
        self._heart_btn.setVisible(False)
        self._heart_btn.clicked.connect(self._on_heart_clicked)
        close_bar.addWidget(self._heart_btn)
        self._close_btn = QToolButton()
        self._close_btn.setText("✕")
        self._close_btn.setStyleSheet(
            "QToolButton { color: white; background: #2b2b2b; border: none;"
            " font-size: 15px; padding: 3px 10px; }"
            "QToolButton:hover { background: #c00; }"
        )
        self._close_btn.clicked.connect(self._do_close)
        close_bar.addWidget(self._close_btn)
        right_layout.addLayout(close_bar)

        # ── scrollable metadata ───────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        # Long Description values would otherwise let the inner widget grow
        # horizontally and shift the row labels (e.g. "Description:") under
        # the left image area.  Forbid the horizontal scrollbar so the
        # content has to wrap inside the available right-panel width.
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: #111; }")
        self._meta_widget = QWidget()
        self._rebuild_meta(title, info_rows)
        right_layout.addWidget(self._scroll, 1)

        # ── action bar ────────────────────────────────────────────────────
        action_bar = QWidget()
        action_bar.setStyleSheet("background: #0d0d0d; border-top: 1px solid #2a2a2a;")
        ab_layout = QVBoxLayout(action_bar)
        ab_layout.setContentsMargins(10, 8, 10, 10)
        ab_layout.setSpacing(6)

        self.btn_open_web = QPushButton("🌐  Open on website")
        self.btn_download = QPushButton("⬇  Download")
        self.btn_send_sd  = QPushButton("💾  Send to SD card")
        # Optional emulator launch buttons, shown directly under "Send to SD
        # card" only when the emulator is detected (and the source pane allows
        # it).  Wired/shown later via set_emulator_actions().
        self.btn_launch_cspect = QPushButton("🕹  Launch CSpect")
        self.btn_launch_mame   = QPushButton("🕹  Launch Mame")
        self.btn_send_ns  = QPushButton("🔁  Send via NextSync")
        self.btn_open_web.setStyleSheet(self._BTN_STYLE)
        self.btn_open_web.setEnabled(False)
        self.btn_open_web.setVisible(False)
        ab_layout.addWidget(self.btn_open_web)
        for btn in (self.btn_download, self.btn_send_sd,
                    self.btn_launch_cspect, self.btn_launch_mame,
                    self.btn_send_ns):
            btn.setStyleSheet(self._BTN_STYLE)
            btn.setEnabled(False)
            ab_layout.addWidget(btn)
        # Hidden until a caller explicitly enables them.
        self.btn_launch_cspect.setVisible(False)
        self.btn_launch_mame.setVisible(False)

        right_layout.addWidget(action_bar)
        root.addWidget(right_panel, 0)

        # ── slideshow timer ───────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(4000)
        self._timer.timeout.connect(self._go_next)

        self._update_nav()
        if self._screenshots:
            self._show_index(0)
            # Proactively validate every URL so broken ones are pruned
            # before the cycling timer reaches them.
            self._prefetch_all()

    # ── public API ────────────────────────────────────────────────────────

    def install_into_stack(self, stack: QStackedWidget, close_fn=None):
        """Add this widget to *stack* (if not already there) and show it."""
        self._close_fn = close_fn
        if stack.indexOf(self) == -1:
            stack.addWidget(self)
        stack.setCurrentWidget(self)
        self.setFocus()
        self._ensure_alien_overlay()
        if len(self._screenshots) > 1:
            self._timer.start()

    def _ensure_alien_overlay(self):
        """When the optional Alien Floyd's background mode is on, float a
        transparent animation overlay (alien Floyds flying above the image plus
        the bottom defending ship) over the screenshot area."""
        try:
            import zxnu_pygame as _zpg
            on = _zpg.alien_floyd_enabled()
        except Exception:
            return
        if on and self._alien_overlay is None:
            try:
                ov = _zpg.AlienFloydWidget(self._img_lbl, transparent=True)
            except Exception:
                return
            self._alien_overlay = ov
            self._position_alien_overlay()
            ov.show()
            ov.start()
            self._position_tag_overlay()   # keep tag chips above the overlay
        elif not on and self._alien_overlay is not None:
            self._destroy_alien_overlay()

    def _destroy_alien_overlay(self):
        ov = self._alien_overlay
        self._alien_overlay = None
        if ov is not None:
            try:
                ov.teardown()
                ov.hide()
                ov.setParent(None)
                ov.deleteLater()
            except Exception:
                pass

    def _position_alien_overlay(self):
        ov = getattr(self, "_alien_overlay", None)
        if ov is None:
            return
        ov.setGeometry(self._img_lbl.rect())
        ov.lower()   # above the label's pixmap, below the tag-chip overlay

    def set_screenshots(self, urls: list):
        """Replace screenshot list and restart slideshow."""
        self._timer.stop()
        self._screenshots = list(urls or [])
        self._shot_index  = 0
        self._shot_cache  = {}
        self._update_nav()
        if self._screenshots:
            self._show_index(0)
            if len(self._screenshots) > 1:
                self._timer.start()
            # Proactively fetch every URL so that broken ones (HTTP 404,
            # network error, etc.) are pruned up front rather than only
            # when the cycling timer happens to land on them.
            self._prefetch_all()
        else:
            self._render_placeholder()

    def _prefetch_all(self):
        """Fire an async fetch for every URL in the cycling list so that
        unreachable images get removed from the list ASAP."""
        if not self._extra_fetch_cb:
            return
        for url in list(self._screenshots):
            if url in self._shot_cache:
                continue
            def _on_px(pm, _u=url):
                if pm is None or pm.isNull():
                    self._drop_bad_url(_u)
                    return
                self._shot_cache[_u] = pm
                # If this URL is currently displayed, refresh the view.
                if self._screenshots and self._shot_index < len(self._screenshots) \
                        and self._screenshots[self._shot_index] == _u:
                    self._display_pixmap(pm)
            try:
                self._extra_fetch_cb(url, _on_px)
            except Exception:
                self._drop_bad_url(url)

    def refresh_meta(self, title: str, rows: list):
        """Rebuild the metadata scroll panel (called from async callbacks)."""
        self._rebuild_meta(title, rows)

    def set_tags(self, tags):
        """Replace the overlay tag badges shown over the screenshot area."""
        self._tags = [str(t) for t in (tags or []) if t]
        self._refresh_tag_overlay()

    def set_favorite_hooks(self, entry, is_favorite_cb, toggle_favorite_cb):
        """Wire up the heart control to a (de)favorite callback for *entry*."""
        self._fav_entry          = entry
        self._is_favorite_cb     = is_favorite_cb
        self._toggle_favorite_cb = toggle_favorite_cb
        self._refresh_heart()

    def _refresh_heart(self):
        btn = getattr(self, "_heart_btn", None)
        if btn is None:
            return
        if not self._toggle_favorite_cb or self._fav_entry is None:
            btn.setVisible(False)
            return
        is_fav = False
        if self._is_favorite_cb is not None:
            try:
                is_fav = bool(self._is_favorite_cb(self._fav_entry))
            except Exception:
                is_fav = False
        btn.setText("♥" if is_fav else "♡")
        btn.setToolTip("Remove from favorites" if is_fav else "Add to favorites")
        btn.setVisible(True)

    def _on_heart_clicked(self):
        if not self._toggle_favorite_cb or self._fav_entry is None:
            return
        try:
            self._toggle_favorite_cb(self._fav_entry)
        except Exception:
            pass
        self._refresh_heart()

    def _refresh_tag_overlay(self):
        ov = getattr(self, "_tag_overlay", None)
        if ov is None:
            return
        if not self._tags:
            ov.setVisible(False)
            ov.setText("")
            return
        chip_css = (
            "background:#1c3a52;color:#bfe6ff;border:1px solid #2f6f9a;"
            "border-radius:3px;padding:2px 7px;margin:2px 3px;"
            "font-size:10pt;font-weight:600;"
        )
        chips = "&nbsp;".join(
            f"<span style='{chip_css}'>{t}</span>" for t in self._tags
        )
        ov.setText(f"<div style='text-align:right;'>{chips}</div>")
        ov.setVisible(True)
        self._position_tag_overlay()

    def _position_tag_overlay(self):
        ov = getattr(self, "_tag_overlay", None)
        if ov is None or not ov.isVisible():
            return
        pad = 8
        max_w = max(60, self._img_lbl.width() - 2 * pad)
        ov.adjustSize()
        w = min(ov.width(), max_w)
        h = ov.sizeHint().height()
        ov.setGeometry(self._img_lbl.width() - w - pad, pad, w, h)
        ov.raise_()

    def eventFilter(self, obj, ev):
        if obj is self._img_lbl:
            if ev.type() == QEvent.Resize:
                self._position_alien_overlay()
                self._position_tag_overlay()
            elif ev.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                if ev.button() == Qt.LeftButton:
                    self._do_close()
                    return True
        return super().eventFilter(obj, ev)

    def set_actions(self, download_cb=None, send_sd_cb=None, send_ns_cb=None,
                    sd_enabled=False, ns_enabled=False,
                    sd_tooltip="", ns_tooltip=""):
        """Wire action buttons.  Pass None to keep a button disabled."""
        self._wire_btn(self.btn_download, download_cb, True)
        self._wire_btn(self.btn_send_sd,  send_sd_cb,  sd_enabled, sd_tooltip)
        self._wire_btn(self.btn_send_ns,  send_ns_cb,  ns_enabled, ns_tooltip)

    def set_emulator_actions(self, cspect_cb=None, mame_cb=None,
                             cspect_enabled=False, mame_enabled=False,
                             cspect_tooltip="", mame_tooltip=""):
        """Wire and reveal the emulator launch buttons shown under "Send to SD
        card".  A button is shown only when its callback is provided (i.e. the
        emulator was detected); pass None to keep it hidden."""
        self._cspect_enabled = bool(cspect_enabled) and cspect_cb is not None
        self._mame_enabled   = bool(mame_enabled) and mame_cb is not None
        self._wire_btn(self.btn_launch_cspect, cspect_cb,
                       self._cspect_enabled, cspect_tooltip)
        self._wire_btn(self.btn_launch_mame, mame_cb,
                       self._mame_enabled, mame_tooltip)
        self.btn_launch_cspect.setVisible(cspect_cb is not None)
        self.btn_launch_mame.setVisible(mame_cb is not None)

    def set_open_web_url(self, url: str, site_label: str = ""):
        """Wire (and show) the 'Open on website' button.  The button opens
        *url* in the user's default external browser via ``webbrowser.open``.
        When *url* is empty the button is hidden."""
        url = (url or "").strip()
        try:
            with __import__("warnings").catch_warnings():
                __import__("warnings").simplefilter("ignore", RuntimeWarning)
                self.btn_open_web.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        if not url:
            self.btn_open_web.setVisible(False)
            self.btn_open_web.setEnabled(False)
            return
        label = f"🌐  Open on {site_label}" if site_label else "🌐  Open on website"
        self.btn_open_web.setText(label)
        self.btn_open_web.setToolTip(url)
        self.btn_open_web.setVisible(True)
        self.btn_open_web.setEnabled(True)
        def _go(_=False, _u=url):
            try:
                webbrowser.open(_u, new=2)
            except Exception:
                pass
        self.btn_open_web.clicked.connect(_go)

    def set_download_available(self, has_dl: bool):
        """Show or hide the download / SD / NextSync action buttons depending
        on whether any downloadable files exist for this entry.

        The emulator launch buttons are intentionally *not* affected here:
        their visibility is governed solely by set_emulator_actions() (i.e.
        emulator detection and the per-pane enable flag), independent of
        whether this particular entry has downloadable files."""
        for btn in (self.btn_download, self.btn_send_sd, self.btn_send_ns):
            btn.setVisible(bool(has_dl))

    # ── private helpers ───────────────────────────────────────────────────

    def _wire_btn(self, btn, cb, enabled, tooltip=""):
        import warnings
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                btn.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        btn.setEnabled(bool(enabled) and cb is not None)
        if tooltip:
            btn.setToolTip(tooltip)
        if cb is not None:
            btn.clicked.connect(cb)

    def set_placeholder(self, label: str, subtitle: str = ""):
        """Store the label/subtitle for the placeholder shown when no screenshots
        are available, and render it immediately if the image area is empty."""
        self._placeholder_label    = str(label or "")
        self._placeholder_subtitle = str(subtitle or "") or self._title
        if not self._screenshots:
            self._render_placeholder()

    def _render_placeholder(self):
        """Render a typed placeholder pixmap in the image area (same style as
        gallery thumbnails: yellow label + subtitle on dark background)."""
        label    = self._placeholder_label or "FILE"
        subtitle = self._placeholder_subtitle or self._title
        pm = zxfmt_make_placeholder_pixmap(label, subtitle)
        if pm and not pm.isNull():
            sz = self._img_lbl.size()
            if sz.width() > 0 and sz.height() > 0:
                self._img_lbl.setPixmap(
                    pm.scaled(sz, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            else:
                self._img_lbl.setPixmap(pm)
            self._img_lbl.setText("")
        else:
            self._img_lbl.setPixmap(QPixmap())
            self._img_lbl.setText("No preview available")
        self._position_tag_overlay()

    def _rebuild_meta(self, title: str, rows: list):
        meta_widget = QWidget()
        meta_widget.setStyleSheet("background: #111;")
        # Constrain the inner widget so it can never grow wider than the
        # right panel; combined with setMinimumWidth(0) on each row label
        # this forces text to wrap rather than extend off-screen.
        meta_widget.setMaximumWidth(380)
        ml = QVBoxLayout(meta_widget)
        ml.setContentsMargins(16, 12, 16, 12)
        ml.setSpacing(6)

        title_lbl = QLabel(title or "")
        tf = title_lbl.font()
        _tps = tf.pointSize()
        if _tps <= 0:
            _tps = max(QFontInfo(tf).pointSize(), 9)
        tf.setPointSize(_tps + 4)
        tf.setBold(True)
        title_lbl.setFont(tf)
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet("color: #fff; margin-bottom: 8px;")
        ml.addWidget(title_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #2e2e2e;")
        ml.addWidget(sep)

        import html as _html
        import re as _re

        for row in (rows or []):
            label, value = row[0], row[1]
            is_html = row[2] if len(row) > 2 else False
            if not value:
                continue

            # Always sanitise the value: strip every HTML tag and decode
            # entities before re-escaping. All rows are rendered inside a
            # Qt.RichText label, so escaped entities (e.g. &lt;LI&gt;) would
            # be decoded by Qt and show as visible tags. The is_html flag
            # merely signals that the raw value *may* contain HTML markup;
            # the plain-text path must apply the same treatment because any
            # value sourced from an API can carry incidental HTML.
            raw = str(value or "")
            raw = _re.sub(r"<br\s*/?>", "\n", raw, flags=_re.IGNORECASE)
            raw = _re.sub(r"</p\s*>",   "\n\n", raw, flags=_re.IGNORECASE)
            raw = _re.sub(r"<li\s*/?>", "\n• ", raw, flags=_re.IGNORECASE)
            raw = _re.sub(r"<[^>]+>",   "",    raw)
            raw = _html.unescape(raw)
            # Collapse excessive blank runs.
            raw = _re.sub(r"\n{3,}", "\n\n", raw).strip()
            val_html = _html.escape(raw).replace("\n", "<br>")

            row_lbl = QLabel(
                '<span style="color:#888; font-weight:bold;">'
                + _html.escape(str(label)) + '</span><br>'
                '<span style="color:#ddd; word-wrap:break-word; '
                'word-break:break-all;">' + val_html + '</span>'
            )
            row_lbl.setTextFormat(Qt.RichText)
            row_lbl.setWordWrap(True)
            row_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            row_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row_lbl.setOpenExternalLinks(True)
            # Critical: Ignored horizontal policy + minimum width 0 means
            # the label cannot inflate to its unwrapped sizeHint() width,
            # so long unbroken text MUST wrap inside the available space.
            row_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.MinimumExpanding)
            row_lbl.setMinimumWidth(0)
            row_lbl.setMaximumWidth(340)
            row_lbl.setContentsMargins(0, 2, 0, 6)
            ml.addWidget(row_lbl)

        ml.addStretch()
        old = self._scroll.widget()
        self._scroll.setWidget(meta_widget)
        if old:
            old.deleteLater()
        self._meta_widget = meta_widget

    def _do_close(self):
        self._timer.stop()
        if self._close_fn:
            self._close_fn()

    # ── keyboard ──────────────────────────────────────────────────────────

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self._do_close()
        elif ev.key() == Qt.Key_Left:
            self._go_prev()
        elif ev.key() == Qt.Key_Right:
            self._go_next()
        else:
            super().keyPressEvent(ev)

    # ── navigation ────────────────────────────────────────────────────────

    def _go_prev(self):
        if not self._screenshots:
            return
        self._timer.stop()
        self._shot_index = (self._shot_index - 1) % len(self._screenshots)
        self._show_index(self._shot_index)
        self._timer.start()

    def _go_next(self):
        if not self._screenshots:
            return
        self._timer.stop()
        self._shot_index = (self._shot_index + 1) % len(self._screenshots)
        self._show_index(self._shot_index)
        self._timer.start()

    def _update_nav(self):
        multi = len(self._screenshots) > 1
        self._prev_btn.setVisible(multi)
        self._next_btn.setVisible(multi)
        self._shot_counter.setText(
            f"{self._shot_index + 1} / {len(self._screenshots)}" if self._screenshots else ""
        )

    def _show_index(self, idx: int):
        if not self._screenshots:
            return
        if idx < 0 or idx >= len(self._screenshots):
            idx = 0
        url = self._screenshots[idx]
        self._shot_index = idx
        self._update_nav()
        cached = self._shot_cache.get(url)
        if cached is not None:
            self._display_pixmap(cached)
            return
        self._img_lbl.setPixmap(QPixmap())
        self._img_lbl.setText("Loading…")
        if self._extra_fetch_cb:
            def _on_px(pm, _u=url):
                if pm is None or pm.isNull():
                    # The image failed to download (e.g. HTTP 404).
                    # Drop it from the cycling list so the slideshow
                    # never shows a black frame for that slot, and
                    # advance to a usable neighbour.
                    self._drop_bad_url(_u)
                    return
                self._shot_cache[_u] = pm
                if self._screenshots and self._shot_index < len(self._screenshots) \
                        and self._screenshots[self._shot_index] == _u:
                    self._display_pixmap(pm)
            try:
                self._extra_fetch_cb(url, _on_px)
            except Exception:
                self._drop_bad_url(url)

    def _drop_bad_url(self, url: str):
        """Remove a URL that failed to load from the cycling list."""
        try:
            i = self._screenshots.index(url)
        except ValueError:
            return
        del self._screenshots[i]
        self._shot_cache.pop(url, None)
        if not self._screenshots:
            self._timer.stop()
            self._shot_index = 0
            self._update_nav()
            self._render_placeholder()
            return
        # Pick a new index that still points to a valid entry.
        new_idx = self._shot_index
        if i < self._shot_index:
            new_idx = self._shot_index - 1
        if new_idx >= len(self._screenshots):
            new_idx = 0
        self._shot_index = new_idx
        self._update_nav()
        if len(self._screenshots) <= 1:
            self._timer.stop()
        self._show_index(new_idx)

    def _display_pixmap(self, pm: QPixmap):
        if pm is None or pm.isNull():
            return
        sz = self._img_lbl.size()
        self._img_lbl.setPixmap(
            pm.scaled(sz, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self._img_lbl.setText("")
        self._position_tag_overlay()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._screenshots and self._shot_index < len(self._screenshots):
            cached = self._shot_cache.get(self._screenshots[self._shot_index])
            if cached:
                self._display_pixmap(cached)
        elif not self._screenshots and self._placeholder_label:
            self._render_placeholder()

    def hideEvent(self, ev):
        self._timer.stop()
        if self._alien_overlay is not None:
            self._alien_overlay.stop()
        super().hideEvent(ev)


def _gallery_viewer_refresh_meta(viewer: "GalleryItemViewer",
                                  title: str, rows: list):
    """Rebuild the metadata scroll area of an already-shown GalleryItemViewer.

    Delegates to viewer.refresh_meta() which handles the widget swap cleanly.
    Called from async callbacks after the viewer is already embedded in the pane.
    """
    try:
        viewer.refresh_meta(title, rows)
    except Exception:
        pass
class BackgroundWidget(QWidget):
    """A QWidget that paints a chosen (or randomly cycling) image from the
    same directory as the script, scaled to fill the entire widget area, blended at
    a configurable opacity level (0–100 %, default 45 %).

    Modes
    -----
    Random (default): cycles through all available background images every
    BG_CYCLE_INTERVAL_MS milliseconds using a QTimer.
    Fixed: a specific image path is set via set_bg_image(path); the timer stops.
    """

    DEFAULT_OPACITY      = 45
    BG_CYCLE_INTERVAL_MS = 5000   # 5 seconds

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._alien_mode  = False   # Alien Floyd's pygame background active
        self._alien_child = None    # AlienFloydWidget child (lazily created)
        self._bg_pixmap   = None
        self._bg_opacity  = self.DEFAULT_OPACITY  # percent 0-100
        self._bg_paths    = []                     # all discovered image paths
        self._bg_index    = -1                     # current index for cycling
        self._bg_fixed    = False                  # True = specific image locked
        self._cycle_timer = QTimer(self)
        self._cycle_timer.setInterval(self.BG_CYCLE_INTERVAL_MS)
        self._cycle_timer.timeout.connect(self._cycle_next)
        # Start in random-cycling mode immediately
        self._bg_paths = self._discover_backgrounds()
        if self._bg_paths:
            import random
            self._bg_index = random.randrange(len(self._bg_paths))
            self._bg_pixmap = self._load_path(self._bg_paths[self._bg_index])
            self._cycle_timer.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_bg_opacity(self, percent: int):
        """Set background image opacity (0 = invisible, 100 = fully opaque)."""
        self._bg_opacity = max(0, min(100, int(percent)))
        self.update()

    def set_bg_image(self, path: str):
        """Switch to a fixed image (non-empty path) or back to random cycling
        (empty / None path).  *path* should be an absolute file path."""
        if path:
            self._bg_fixed = True
            self._cycle_timer.stop()
            self._bg_pixmap = self._load_path(path)
            self.update()
        else:
            self._bg_fixed = False
            self._bg_paths = self._discover_backgrounds()
            if self._bg_paths:
                import random
                self._bg_index = random.randrange(len(self._bg_paths))
                self._bg_pixmap = self._load_path(self._bg_paths[self._bg_index])
                self._cycle_timer.start()
            else:
                self._bg_pixmap = None
                self.update()

    def bg_paths(self) -> list:
        """Return the list of discovered background image paths (may be empty)."""
        return list(self._bg_paths)

    # ------------------------------------------------------------------
    # Alien Floyd's animated background (optional, pygame-ce)
    # ------------------------------------------------------------------

    def set_alien_mode(self, enabled: bool):
        """Enable/disable the optional pygame-ce "Alien Floyd's" animated
        background.  When enabled it replaces the cycling background images on
        every tab (a full-bleed opaque animation child behind the tab widget);
        when disabled the image cycling resumes."""
        enabled = bool(enabled)
        if enabled == self._alien_mode:
            return
        if enabled:
            try:
                from zxnu_pygame import AlienFloydWidget, pygame_available
                ok, _why = pygame_available()
                if not ok:
                    return
            except Exception:
                return
            self._alien_mode = True
            self._cycle_timer.stop()
            if self._alien_child is None:
                self._alien_child = AlienFloydWidget(self)
            self._alien_child.setGeometry(self.rect())
            self._alien_child.lower()          # sit behind the tab widget
            self._alien_child.show()
            self._alien_child.start()
            self.update()
        else:
            self._alien_mode = False
            if self._alien_child is not None:
                try:
                    self._alien_child.teardown()
                    self._alien_child.hide()
                    self._alien_child.setParent(None)
                    self._alien_child.deleteLater()
                except Exception:
                    pass
                self._alien_child = None
            # Resume image cycling unless a fixed image is locked.
            if not self._bg_fixed and self._bg_paths:
                self._cycle_timer.start()
            self.update()

    def alien_mode(self) -> bool:
        return self._alien_mode

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._alien_child is not None:
            self._alien_child.setGeometry(self.rect())
            self._alien_child.lower()

    def showEvent(self, event):
        super().showEvent(event)
        if self._alien_mode and self._alien_child is not None:
            self._alien_child.start()

    def hideEvent(self, event):
        if self._alien_child is not None:
            self._alien_child.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _discover_backgrounds() -> list:
        """Return a list of image paths from the script directory first,
        then from Qt embedded resources (rc_backgrounds)."""
        image_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
        paths = []

        # Filesystem images (same directory as the running script/exe)
        bg_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        if os.path.isdir(bg_dir):
            paths.extend(sorted(
                os.path.join(bg_dir, f)
                for f in os.listdir(bg_dir)
                if os.path.splitext(f)[1].lower() in image_extensions
            ))

        # Qt resource images (embedded via rc_backgrounds)
        from PySide6.QtCore import QDir as _QDir
        for _name in _QDir(":/").entryList():
            if os.path.splitext(_name)[1].lower() in image_extensions:
                rc_path = ":/" + _name
                if rc_path not in paths:
                    paths.append(rc_path)

        return paths

    @staticmethod
    def _load_path(path: str):
        if not path:
            return None
        px = QPixmap(path)
        return px if not px.isNull() else None

    def _cycle_next(self):
        """Advance to the next image in the rotation."""
        if not self._bg_paths:
            return
        self._bg_index = (self._bg_index + 1) % len(self._bg_paths)
        self._bg_pixmap = self._load_path(self._bg_paths[self._bg_index])
        self.update()

    # kept for backward-compat (not used internally anymore)
    @staticmethod
    def _load_random_background():
        import random
        bg_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        if not os.path.isdir(bg_dir):
            return None
        image_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
        candidates = [
            os.path.join(bg_dir, f)
            for f in os.listdir(bg_dir)
            if os.path.splitext(f)[1].lower() in image_extensions
        ]
        if not candidates:
            return None
        chosen = random.choice(candidates)
        px = QPixmap(chosen)
        return px if not px.isNull() else None

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._bg_pixmap and self._bg_opacity > 0:
            painter = QPainter(self)
            painter.setOpacity(self._bg_opacity / 100.0)
            painter.drawPixmap(self.rect(), self._bg_pixmap)


# Export every public/private module-level name (including the
# underscore-prefixed helpers and caches) so `from <module> import *`
# in the main file picks them all up.
__all__ = [_n for _n in dir() if not _n.startswith('__')]
