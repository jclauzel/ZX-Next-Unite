"""The editable path boxes that remember where they have been.

Three boxes in the app let the user type a path and pick a recent one back
out of a dropdown: the SD Card tab's disk-image box (9.6.0) and, since
9.7.22, the two LOCAL FOLDER boxes - the SD Card tab's "Local path:" and the
Remote Explorer's sync-root box.

They share one widget rather than three hand-kept copies, because swapping a
QLineEdit for a QComboBox brings a set of behaviours that each had to be
measured and worked around: a popup view built in C++ that ignores an
assigned ``keyPressEvent``, a container that selects a row on ANY button
release, an inline completer that rewrites what is being typed, a wheel notch
that counts as a deliberate pick, and a line edit that reports
``editingFinished`` when nobody committed anything. Each of those cost a bug;
all of them are documented on the classes below, once.

Split out of ``zxnu_workers.py`` in 9.7.22 so that module stays about
background work. The dependency runs ONE way - this module imports
``CompactButton`` from there for the '✕' button, and nothing there imports
this - so there is no cycle.
"""

import os
import time

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtWidgets import QComboBox, QMenu, QMessageBox

from zxnu_config import MAX_PATH_HISTORY, normalize_history_folder
from zxnu_i18n import ui_tr_now
# The '✕' is a CompactButton for the same reason the explorers' Up/Refresh
# buttons are: it sizes from its own glyph, so the retro font setting cannot
# squeeze it out of the row.
from zxnu_workers import CompactButton


class PathHistoryCombo(QComboBox):
    """An editable path box that remembers where it has been (9.6.0).

    Born as the SD-image path box and generalised in 9.7.22, when the two
    LOCAL FOLDER boxes — the SD Card tab's "Local path:" and the Remote
    Explorer's sync-root box — grew the same memory. This class owns the
    dropdown behaviour and the removal affordances ONLY; what a remembered
    path *means*, and what happens when one is picked or forgotten, belongs
    to the subclass (:class:`FolderHistoryCombo` below, which also owns its
    own list) or to the wiring around it (``zxnu_main._ImagePathCombo``,
    whose removals must also unload the disk).

    Subclasses set :attr:`canonicalize` to the comparison spelling for their
    kind of path, so ``history_index`` matches the way that filesystem does
    rather than by raw text.

    showPopup stamps WHEN the history dropdown opened, so the activation
    wiring can tell a real pick from the Windows "phantom activation" — the
    very click that opens the dropdown also "activating" the current entry
    when the (long-path-widened) popup lands under the cursor. Reported as:
    the history list appeared and instantly vanished while the already-loaded
    image reloaded. See the imageinput.activated wiring in MainWindow.setupUI
    and ``FolderHistoryCombo._on_activated``.

    It also owns the history-REMOVAL affordances (9.6.0). The remembered
    paths used to be write-only: clearing the line edit and pressing Enter
    unloaded the image but the stale path was still sitting in the dropdown
    on the next click, with no way at all to forget it (reported). Three
    ways out now, and every one of them ends in ``removeIndexRequested`` /
    ``clearHistoryRequested`` so ONE writer owns the list — the closures
    wired in MainWindow.setupUI for the image box, this class's own
    subclass for the folder boxes:

      * the '✕' button beside the box — forget the path that is SHOWN;
      * Delete on the highlighted row of the open dropdown — forget that
        row in place, with the popup staying up for the next one;
      * right-click, on the box (text area OR arrow) or on a dropdown row —
        'Remove "<path>" from the list' / 'Clear the whole list'. On the box
        the actions are appended to the line edit's own standard menu, so
        Cut/Copy/Paste survive.

    Four Qt rules are load-bearing here, and three of them cost a rewrite:

    * The dropdown view is created in C++ by ``QComboBoxPrivateContainer``.
      PySide6 dispatches a virtual to a Python attribute only for objects
      INSTANTIATED FROM PYTHON, so the house style used on the explorers'
      trees — ``view.keyPressEvent = my_handler`` — is silently dead code
      here. An event filter is the only mechanism that reaches it, and this
      combo, being Python-made, is a valid filter target. ShortcutOverride
      arrives first carrying the same key(), so only KeyPress is acted on.
    * That same container's mouse handler does NOT check which button was
      released: any release over a row calls hidePopup() and selects it. So
      a right-click on a dropdown entry LOADED that image instead of
      offering to forget it. The filter swallows the right button outright
      and drives the menu from the PRESS.
    * ``customContextMenuRequested`` is not used on the popup for the same
      reason: on Windows the context-menu event is synthesised on RELEASE,
      i.e. after the popup has already closed, and its position maps
      through a hidden viewport.
    * QComboBox forces its line edit to NoContextMenu and builds the menu
      itself, so ONE ``contextMenuEvent`` override covers the text area and
      the drop-down arrow together.

    A QMenu is never exec'd inside the dropdown's Qt::Popup grab: the row
    and the screen position are captured, the popup is dismissed, and the
    menu opens on the next event-loop turn. The first 250 ms after the
    popup opens are refused outright — inside that window Windows' combo
    animation makes hidePopup() a no-op (the popup comes back up anyway),
    which is exactly how a nested grab would wedge the UI.
    """

    removeIndexRequested = Signal(int)
    clearHistoryRequested = Signal()
    #: 'Remember this folder' — offered in the right-click menu only by boxes
    #: that say so, because it only makes sense where the box FOLLOWS a tree
    #: (the SD Card local pane) rather than showing a deliberate choice.
    rememberRequested = Signal()

    #: Subclass hook: the spelling two paths are COMPARED under. The default
    #: is a bare strip, so a subclass that forgets to set it still behaves
    #: like plain text rather than raising.
    canonicalize = staticmethod(lambda s: (s or "").strip())

    #: See :attr:`rememberRequested`. Off unless a subclass/instance opts in.
    offer_remember = False

    # Right-clicks inside this window after the dropdown opened are refused:
    # see the class docstring (hidePopup() is a no-op while the open
    # animation runs, and a menu over a live popup is a nested grab).
    _POPUP_SETTLE_S = 0.25

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup_shown_at = 0.0
        self._armed_view = None
        self._opening_popup = False

    # ---- history helpers --------------------------------------------------

    def history_index(self, text):
        """Row of *text* in the remembered list, or -1.

        Compared the way the filesystem compares: :attr:`canonicalize` first
        (stray quotes, separators) and then os.path.normcase, because
        C:\\TEMP\\next.img and C:\\temp\\next.img are one path on Windows and
        QComboBox.findText would call them two."""
        wanted = os.path.normcase(self.canonicalize(text))
        if not wanted:
            return -1
        for i in range(self.count()):
            if os.path.normcase(self.canonicalize(self.itemText(i))) == wanted:
                return i
        return -1

    @staticmethod
    def _menu_path_label(path, limit=64):
        """A path made fit for a menu label: shortened around the middle so
        the drive and the file name survive (a full image path can be wider
        than the screen), and '&' doubled — Qt reads a single one as the
        mnemonic marker, which ate the ampersand in 'Rock & Roll'."""
        text = str(path)
        if len(text) > limit:
            head, tail = text[:limit // 3], text[-(limit - limit // 3 - 1):]
            text = f"{head}…{tail}"
        return text.replace("&", "&&")

    # ---- the dropdown's event filter --------------------------------------

    def _arm_popup_view(self):
        """(Re)install the dropdown hooks. self.view() is only guaranteed to
        be the same object until something calls setView/setEditable, so the
        arming is idempotent and keyed on the view identity. Both the view
        (keys) and its viewport (mouse) are filtered."""
        view = self.view()
        if view is None or self._armed_view is view:
            return
        self._armed_view = view
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        view = self._armed_view
        if view is not None and obj in (view, view.viewport()):
            etype = event.type()
            # Delete forgets the highlighted row in place. KeyPress only:
            # ShortcutOverride arrives first with the same key() and would
            # fire the removal twice.
            if etype == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Delete:
                    row = view.currentIndex().row()
                    if 0 <= row < self.count():
                        self.removeIndexRequested.emit(row)
                        self.refresh_open_popup(row)
                    return True
            # The container selects (and so LOADS) a row on ANY button
            # release, right one included. Swallow the whole right-button
            # gesture and open the menu from the press instead.
            elif etype in (QEvent.Type.MouseButtonPress,
                           QEvent.Type.MouseButtonRelease,
                           QEvent.Type.MouseButtonDblClick):
                if event.button() == Qt.MouseButton.RightButton:
                    if etype == QEvent.Type.MouseButtonPress:
                        self._popup_menu_from_press(view, event)
                    return True
        return super().eventFilter(obj, event)

    def _popup_menu_from_press(self, view, event):
        """Right-press on a dropdown row: remember what was hit, then open
        the menu once the popup's grab is gone."""
        if time.monotonic() - self._popup_shown_at < self._POPUP_SETTLE_S:
            return                      # see _POPUP_SETTLE_S
        # indexAt() wants VIEWPORT coordinates, and a mouse event delivered
        # to either the view or its viewport carries viewport ones here (the
        # scroll area hands the event on unchanged) — so no remap: the
        # frame-width remap this started life with picked the row ABOVE on
        # every row boundary.
        row = view.indexAt(event.position().toPoint()).row()
        where = event.globalPosition().toPoint()
        self.hidePopup()
        QTimer.singleShot(0, lambda: self._exec_history_menu(where, row))

    def showPopup(self):
        self._popup_shown_at = time.monotonic()
        self._arm_popup_view()
        # _opening_popup is read by FolderHistoryCombo's commit gate: Qt emits
        # the line edit's editingFinished from INSIDE this call (measured on
        # Windows), which must never be mistaken for the user committing.
        self._opening_popup = True
        try:
            super().showPopup()
        finally:
            self._opening_popup = False

    def wheelEvent(self, event):
        """Swallow the wheel. A QComboBox changes row on every notch and
        reports it as an `activated` — i.e. as a deliberate pick — so a stray
        scroll over the box would silently load a different disk image, or
        re-root an explorer (measured: one notch moved the row AND fired
        activated). The QLineEdit these boxes replaced ignored the wheel, and
        the dropdown is still scrollable while it is OPEN, because the popup
        view handles its own wheel events."""
        event.ignore()

    def refresh_open_popup(self, keep_row=-1):
        """Re-lay an OPEN dropdown after a row was removed underneath it.

        The container is sized when it opens, so a row removed under it
        leaves a blank strip; showPopup() on an already-open popup
        re-measures it in place, with no hide/show flicker. No-op when the
        popup is closed (the '✕' button's path) or while the box is locked
        mid-load — a live dropdown over a disabled combo can still be
        clicked, and that click would re-enter load_image()."""
        try:
            view = self.view()
            if view is None or not view.isVisible() or not self.isEnabled():
                return
            if not self.count():
                self.hidePopup()
                return
            self.showPopup()
            row = min(max(keep_row, 0), self.count() - 1)
            self.view().setCurrentIndex(
                self.model().index(row, self.modelColumn()))
        except RuntimeError:
            pass

    # ---- the menus --------------------------------------------------------

    def contextMenuEvent(self, event):
        """Right-click on the BOX. One override covers the text area and the
        drop-down arrow: QComboBox pins its line edit to NoContextMenu and
        builds the menu itself, so both land here."""
        line = self.lineEdit()
        menu = line.createStandardContextMenu() if line is not None else None
        if menu is not None:
            menu.setParent(self, menu.windowFlags())
        event.accept()
        self._exec_history_menu(event.globalPos(),
                                self.history_index(self.currentText()),
                                menu=menu)

    def _exec_history_menu(self, global_pos, row, menu=None):
        if menu is None:
            menu = QMenu(self)
        elif not menu.isEmpty():
            menu.addSeparator()
        # 'Remember this folder' leads, because on a box that follows a tree
        # it is the only way a path ever ENTERS the list by hand: without it
        # a user who navigates purely by double-click never sees an entry
        # appear and reads the whole feature as broken.
        if self.offer_remember:
            act_remember = menu.addAction(ui_tr_now("Remember this folder"))
            act_remember.triggered.connect(
                lambda _checked=False: self.rememberRequested.emit())
            menu.addSeparator()
        if 0 <= row < self.count():
            act_remove = menu.addAction(
                ui_tr_now('Remove "{path}" from the list').format(
                    path=self._menu_path_label(self.itemText(row))))
            act_remove.triggered.connect(
                lambda _checked=False, r=row: self.removeIndexRequested.emit(r))
        act_clear = menu.addAction(ui_tr_now("Clear the whole list"))
        act_clear.setEnabled(self.count() > 0)
        # Deferred: the confirm dialog must not open inside the menu's own
        # input grab (see the NextSync explorer menu for the same rule).
        act_clear.triggered.connect(
            lambda _checked=False: QTimer.singleShot(
                0, self.clearHistoryRequested.emit))
        menu.exec(global_pos)
        menu.deleteLater()


class FolderHistoryCombo(PathHistoryCombo):
    """A local-FOLDER path box that remembers where it has been (9.7.22).

    The SD Card tab's "Local path:" box and the Remote Explorer's sync-root
    box were plain ``QLineEdit``s whose paths were forgotten the moment the
    user moved on, while the SD-image box beside them had remembered its
    paths since 9.6.0 (reported: "do the same for the local path"). This is
    that memory, as ONE widget rather than two hand-kept copies - the four
    load-bearing Qt rules live once, on :class:`PathHistoryCombo`.

    Unlike the image box, this one OWNS its list. Forgetting a folder has no
    side effects beyond the list itself (nothing unmounts, no tree moves, no
    sync root changes), so the base's ``removeIndexRequested`` /
    ``clearHistoryRequested`` are wired straight to this class's own
    ``remove_index`` / ``clear_history`` and the host only has to persist the
    result through one ``on_changed`` callback. The image box keeps its
    signals-out contract precisely because its removals DO have side effects.

    Two Qt facts shape the implementation, both measured rather than assumed:

    * On an editable combo ``setCurrentText`` is exactly ``setEditText`` - it
      never inserts a row and never moves ``currentIndex``, under ANY insert
      policy. So the mirror writes the two panes already make on every tree
      move are harmless, and ``currentIndex`` is NOT a reliable answer to
      "which row is showing": everything here resolves through
      ``history_index(currentText())`` instead.
    * ``setInsertPolicy(NoInsert)`` is still mandatory, for the ENTER key
      alone: ``lineEdit().returnPressed`` reaches QComboBox's own handler,
      which under the default policy APPENDS the typed text as a row. Both
      boxes commit on Enter, so without NoInsert every typed path would be
      remembered twice - once by us, once behind our back.

    The ``QLineEdit`` compatibility surface below is what keeps this a drop-in
    replacement: ``text`` / ``setText`` / ``editingFinished`` /
    ``setClearButtonEnabled`` do not exist on ``QComboBox`` at all, so these
    shadow nothing, and every existing call site and test keeps working.
    """

    #: Forwarded from the line edit so ``editingFinished.connect(...)`` keeps
    #: working on call sites (and ``.emit()`` in tests) that predate the combo.
    editingFinished = Signal()
    #: A row picked from the dropdown, as the raw stored text. Driven from
    #: ``activated`` and NEVER ``currentIndexChanged``: the panes write this
    #: box on every tree move, and through currentIndexChanged each of those
    #: writes would re-enter navigation.
    pathActivated = Signal(str)

    canonicalize = staticmethod(normalize_history_folder)

    def __init__(self, parent=None, *, cap=MAX_PATH_HISTORY, on_changed=None,
                 current_path=None, log=None, offer_remember=False,
                 clear_button=True):
        """*on_changed* is handed the whole list, already '|'-joined and ready
        for hdfg.cfg, whenever it actually changes; *current_path* answers the
        path the box is MIRRORING (used to spot the Windows phantom
        activation); *log* takes one already-translated line."""
        super().__init__(parent)
        self._cap = max(1, int(cap))
        self._on_changed = on_changed or (lambda _raw: None)
        self._current_path = current_path or (lambda: "")
        self._log = log or (lambda _msg: None)
        self.offer_remember = bool(offer_remember)

        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)   # the Enter key
        # A combo sizes itself to its widest ITEM, so one long remembered path
        # would raise the whole column's minimum width and stop the local
        # explorer's splitter being dragged left (the 9.7.2 lesson the SD
        # pane's diskimageexplorerlabel.setMinimumWidth(1) records).
        self.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(8)

        # Qt's editable-combo default is an INLINE completer, which rewrites
        # what is being typed to the first remembered row that starts with it:
        # typing "C:/proj/al" left the box reading "C:/proj/alpha", and
        # committing then navigated somewhere the user never typed (measured).
        # The QLineEdit these boxes replaced had no completer, and the
        # dropdown is the way to reach a remembered path.
        self.setCompleter(None)

        # NOT a straight signal-to-signal forward: the line edit's
        # editingFinished also fires when nobody committed anything - on any
        # focus-out (including the one the box's OWN dropdown causes) and from
        # inside showPopup(). Both were measured re-committing the mirrored
        # text: on the Remote Explorer that re-rooted the browse tree and hid
        # the 'Set current folder…' offer mid-gesture, and on the SD Card box
        # it remembered the folder the user had merely walked to - the exact
        # rule the remember= default exists to enforce. So the forward is
        # gated on the user having actually TYPED since the last write.
        self._user_edited = False
        self.lineEdit().textEdited.connect(self._note_user_edit)
        self.lineEdit().editingFinished.connect(self._maybe_commit)
        self.activated.connect(self._on_activated)
        self.currentTextChanged.connect(lambda _t: self._sync_clear_button())
        self.removeIndexRequested.connect(self.remove_index)
        self.clearHistoryRequested.connect(self.clear_history)

        self.clear_button = None
        if clear_button:
            # CompactButton, not QPushButton: it sizes from its own glyph, so
            # the retro font setting cannot squeeze it out of the row.
            self.clear_button = CompactButton("✕", None, floor=30, padding=14)
            # Plain English on purpose, NOT ui_tr_now: a construction-time
            # tooltip put through ui_tr_now is cached as its own English
            # source on a non-English session and freezes in that language for
            # good (the 9.7.21 splitter-handle rule). translate_widget_tree
            # swaps it at startup and on every language change.
            self.clear_button.setToolTip(
                "Remove the folder path shown on the left from the list.\n"
                "The folder itself is not deleted.")
            self.clear_button.clicked.connect(self._on_clear_clicked)
        self._sync_clear_button()

    # ---- QLineEdit compatibility surface ----------------------------------
    # QComboBox defines none of these names (probed), so nothing is shadowed
    # and every historical call site keeps reading as it did.

    def text(self):
        return self.currentText()

    def setText(self, text):
        self.setCurrentText(text)

    def setCurrentText(self, text):
        # Every write that is not the user typing goes through here - the
        # panes' mirror of the tree, a restore, a removal putting the shown
        # path back - so this is the one place to forget that the user had
        # typed. Without it a half-typed path abandoned earlier would commit
        # the MIRRORED text at the next focus-out.
        self._user_edited = False
        super().setCurrentText(text)

    # ---- "did the user actually commit something?" ------------------------

    def _note_user_edit(self, _text):
        """textEdited fires for USER edits only - never for setText - which is
        exactly the distinction the commit gate needs."""
        self._user_edited = True

    def _maybe_commit(self):
        """Forward the line edit's editingFinished only when it really is a
        commit: the user has typed since the last write, and this is not the
        box's own dropdown stealing the focus."""
        if not self._user_edited:
            return
        if self._opening_popup:
            return
        view = self.view()
        if view is not None and view.isVisible():
            return                      # the popup is up: not a commit
        self._user_edited = False
        self.editingFinished.emit()

    def setPlaceholderText(self, text):
        # MUST land on the line edit: translate_widget_tree's QComboBox branch
        # reads lineEdit().placeholderText() only, and QComboBox's own
        # placeholder means something else entirely (shown when currentIndex
        # is -1), so setting it there drops the text from every language.
        self.lineEdit().setPlaceholderText(text)

    def setClearButtonEnabled(self, on):
        self.lineEdit().setClearButtonEnabled(on)

    # ---- the list ---------------------------------------------------------

    def history_list(self):
        """The remembered paths, most-recent-first, as shown."""
        return [self.itemText(i) for i in range(self.count())]

    def history_to_cfg(self):
        """The list as one hdfg.cfg value: canonical, de-duplicated,
        '|'-joined. Entries the canonicaliser refuses (a folder whose name
        contains the separator itself) are dropped rather than written as
        something that cannot be read back."""
        out = []
        for i in range(self.count()):
            clean = self.canonicalize(self.itemText(i))
            if clean and clean not in out:
                out.append(clean)
        return "|".join(out)

    def set_history_from_cfg(self, raw):
        """Replace the list with a saved value. UNCONDITIONAL, including for
        an empty one: the list can be emptied now, and skipping the clear
        would let a second restore resurrect what the user just forgot.

        The shown text is captured and put back because ``addItem`` into an
        EMPTY editable combo sets currentIndex to 0 and OVERWRITES the line
        edit - measured. Without that, row 0 would masquerade as the committed
        path in a box nothing else writes on a first run."""
        entries = []
        for part in str(raw or "").split("|"):
            clean = self.canonicalize(part)
            if clean and clean not in entries:
                entries.append(clean)
        shown = self.currentText()
        self.blockSignals(True)
        try:
            self.clear()
            for entry in entries[:self._cap]:
                self.addItem(entry)
            self.setCurrentText(shown)
        finally:
            self.blockSignals(False)
        self._sync_clear_button()
        # Deliberately silent: a restore echoing straight back into the file
        # would rewrite hdfg.cfg at startup on every single launch.

    def remember(self, path):
        """Put *path* at the top of the list. Returns True (and notifies)
        only when the list actually CHANGED - a path already at row 0 is not
        a change, which is what keeps a re-commit of the same folder (every
        focus-out, and the Remote Explorer's startup commit) from rewriting
        hdfg.cfg. Never touches the shown text."""
        clean = self.canonicalize(path)
        if not clean:
            return False
        index = self.history_index(clean)
        if index == 0 and self.itemText(0) == clean:
            return False
        # The shown text is captured and put back for the same measured reason
        # as in set_history_from_cfg: inserting the FIRST row into an empty
        # editable combo sets currentIndex to 0 and overwrites the line edit.
        # Caught by a probe - the box is normally re-synced by the pane a
        # moment later, which is exactly what would have hidden it.
        shown = self.currentText()
        self.blockSignals(True)
        try:
            if index >= 0:
                self.removeItem(index)
            self.insertItem(0, clean)
            while self.count() > self._cap:
                self.removeItem(self.count() - 1)
            if self.currentText() != shown:
                self.setCurrentText(shown)
        finally:
            self.blockSignals(False)
        self._sync_clear_button()
        self._on_changed(self.history_to_cfg())
        return True

    def remove_index(self, index):
        """Forget one row. The shown text is restored unconditionally: unlike
        the image box - where forgetting the loaded image must also unload it
        - forgetting a folder must never move the tree or the sync root. It
        has to be restored explicitly because removing the CURRENT row
        rewrites the line edit to the neighbouring one (measured)."""
        if not (0 <= index < self.count()):
            return False
        gone = self.itemText(index)
        shown = self.currentText()
        self.blockSignals(True)
        try:
            self.removeItem(index)
            self.setCurrentText(shown)
        finally:
            self.blockSignals(False)
        self._sync_clear_button()
        self._on_changed(self.history_to_cfg())
        self._log(ui_tr_now(
            "Removed {path} from the list — the folder itself was not "
            "deleted.").format(path=gone))
        return True

    def clear_history(self):
        """'Clear the whole list', with a confirm. Keeps the shown path for
        the same reason as :meth:`remove_index`."""
        count = self.count()
        if not count:
            return False
        if QMessageBox.question(
                self, ui_tr_now("Clear the folder list?"),
                ui_tr_now("Forget all {count} remembered folder paths? The "
                          "folders themselves are not deleted.").format(
                              count=count),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return False
        shown = self.currentText()
        self.blockSignals(True)
        try:
            self.clear()                 # editable: empties the box too
            self.setCurrentText(shown)
        finally:
            self.blockSignals(False)
        self._sync_clear_button()
        self._on_changed(self.history_to_cfg())
        self._log(ui_tr_now("Cleared the folder list — no folders were deleted."))
        return True

    # ---- the '✕' ----------------------------------------------------------

    def _sync_clear_button(self):
        """Live only while the SHOWN path is one the list actually holds.

        Gated on the list, not on emptiness as the image box's twin is: this
        '✕' can only ever forget a row, so with the box showing an unlisted
        folder there is genuinely nothing for it to do."""
        try:
            if self.clear_button is not None:
                self.clear_button.setEnabled(
                    self.history_index(self.currentText()) >= 0)
        except (AttributeError, RuntimeError):
            pass

    def _on_clear_clicked(self):
        index = self.history_index(self.currentText())
        if index >= 0:
            self.remove_index(index)

    # ---- picking a remembered path ----------------------------------------

    def _on_activated(self, index):
        """A dropdown pick. Re-picking the path the box is ALREADY showing is
        the Windows phantom activation's signature (see PathHistoryCombo): the
        click that opens a popup widened by long paths can land on the current
        row. Within half a second of the popup opening it is treated as that
        and the list is put straight back up, so the user gets the list their
        click asked for instead of a silent re-navigation."""
        picked = self.itemText(index)
        if self.canonicalize(picked) == self.canonicalize(
                self._current_path() or ""):
            if time.monotonic() - self._popup_shown_at < 0.5:
                QTimer.singleShot(0, self.showPopup)
            return
        self.pathActivated.emit(picked)
