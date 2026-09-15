# -*- coding: utf-8 -*-
"""The two local path boxes' remembered folders (9.7.22).

The SD Card tab's "Local path:" box and the Remote Explorer's sync-root box
became history combos in 9.7.22 - the folder twins of the SD-image box, which
has remembered its paths since 9.6.0. This suite covers the two pieces the
UI suites reach only indirectly:

* ``normalize_history_folder`` - the canonical spelling both lists are stored
  and COMPARED under, including the two refusals that are easy to lose in a
  later "simplification" (a bare drive root, and the '|' separator);
* ``FolderHistoryCombo`` itself, driven headlessly: remember / dedupe /
  move-to-front / cap, the cfg round trip, and the Qt behaviours that had to
  be worked around rather than assumed.

The offscreen phase 16 and tests/test_remote_explorer_widget.py cover the two
panes' wiring; this one covers the parts underneath them.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402
from zxnu_config import (CONFIG_FILE_SETTINGS, MAX_PATH_HISTORY,  # noqa: E402
                         SETTING_EXPLORERPATH,
                         SETTING_EXPLORERPATH_HISTORY,
                         SETTING_NEXTSYNC_EXPLORERPATH,
                         SETTING_NEXTSYNC_EXPLORERPATH_HISTORY,
                         normalize_history_folder)
from zxnu_pathhistorycombo import (FolderHistoryCombo,  # noqa: E402
                                   PathHistoryCombo)

FAILURES = []


def _wheel_event(widget):
    """One downward wheel notch over *widget*."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    return QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# normalize_history_folder
# ---------------------------------------------------------------------------

def test_canonicaliser():
    print("\n== normalize_history_folder ==")
    n = normalize_history_folder
    bs = chr(92)

    check("blank input answers empty",
          n("") == "" and n(None) == "" and n("   ") == "")
    check("backslashes become forward slashes",
          n("C:" + bs + "temp" + bs + "x") == "C:/temp/x",
          n("C:" + bs + "temp" + bs + "x"))
    check("a trailing slash is dropped so C:/t and C:/t/ are ONE entry",
          n("C:/t/") == n("C:/t") == "C:/t")
    check("surrounding quotes are peeled (old cfg files wrote them)",
          n('"C:/t"') == "C:/t" and n("'C:/t'") == "C:/t")
    check("whitespace is stripped", n("  C:/t  ") == "C:/t")

    # THE drive-root rule: a bare "C:" is a PER-DRIVE relative path on Windows
    # that os.path.isdir still answers True for, so a remembered drive root
    # would quietly navigate somewhere else entirely.
    check("a drive root keeps its slash", n("C:/") == "C:/", n("C:/"))
    check("...and a bare drive letter GAINS one", n("C:") == "C:/", n("C:"))
    check("the POSIX root survives", n("/") == "/", n("/"))
    check("a UNC share is left alone apart from the trailing slash",
          n("//server/share/") == "//server/share", n("//server/share/"))

    # THE separator rule: '|' is illegal in a Windows path but legal in a
    # POSIX folder name, and hdfg.cfg is a strict one-line-per-key file - a
    # value that cannot be read back must never be written.
    check("a folder whose name contains the separator is refused",
          n("/home/u/a|b") == "", n("/home/u/a|b"))

    check("the result is idempotent",
          all(n(n(p)) == n(p) for p in
              ("C:/t/", "C:", "/", "//s/share/", '"C:/t"', "")))


# ---------------------------------------------------------------------------
# the cfg keys
# ---------------------------------------------------------------------------

def test_setting_keys():
    print("\n== the cfg keys ==")
    # Registration is not cosmetic: save_configuration_file opens hdfg.cfg
    # TRUNCATING and then walks CONFIG_FILE_SETTINGS, so an unregistered key
    # round-trips fine all session and vanishes on restart - while a key the
    # dict is missing raises mid-loop and leaves a ZERO-BYTE config.
    for key in (SETTING_EXPLORERPATH_HISTORY,
                SETTING_NEXTSYNC_EXPLORERPATH_HISTORY):
        check(f"{key} is registered in CONFIG_FILE_SETTINGS (persisted + "
              f"pre-seeded)", key in CONFIG_FILE_SETTINGS)
    check("each history key sits beside its current-value sibling",
          CONFIG_FILE_SETTINGS.index(SETTING_EXPLORERPATH) + 1
          == CONFIG_FILE_SETTINGS.index(SETTING_EXPLORERPATH_HISTORY)
          and CONFIG_FILE_SETTINGS.index(SETTING_NEXTSYNC_EXPLORERPATH) + 1
          == CONFIG_FILE_SETTINGS.index(SETTING_NEXTSYNC_EXPLORERPATH_HISTORY))
    # The explicit ask was TWO distinct values: the two panes browse for
    # different reasons, and one shared list would drop a sync root into the
    # SD Card tab's box.
    check("the two lists are distinct keys",
          SETTING_EXPLORERPATH_HISTORY != SETTING_NEXTSYNC_EXPLORERPATH_HISTORY)
    check("no key is registered twice",
          len(CONFIG_FILE_SETTINGS) == len(set(CONFIG_FILE_SETTINGS)))


# ---------------------------------------------------------------------------
# FolderHistoryCombo
# ---------------------------------------------------------------------------

def test_combo_basics():
    print("\n== FolderHistoryCombo: construction and the QLineEdit facade ==")
    c = FolderHistoryCombo(cap=3)
    check("it is a PathHistoryCombo, so the four Qt rules are shared",
          isinstance(c, PathHistoryCombo))
    check("editable", c.isEditable())
    # NoInsert is about the ENTER key: lineEdit().returnPressed reaches
    # QComboBox's own handler, which under the default policy appends the
    # typed text as a row of its own - so every committed path would be
    # remembered twice, once behind our back.
    check("never self-inserts", c.insertPolicy().name == "NoInsert",
          c.insertPolicy().name)

    # None of these names exist on QComboBox, so the facade shadows nothing.
    from PySide6.QtWidgets import QComboBox
    for name in ("text", "setText", "editingFinished", "setClearButtonEnabled"):
        check(f"QComboBox has no {name} of its own to shadow",
              not hasattr(QComboBox, name))
    c.setText("C:/aaa")
    check("setText/text round trip", c.text() == "C:/aaa", c.text())
    c.setPlaceholderText("Local folder path...")
    # It MUST land on the line edit: translate_widget_tree's QComboBox branch
    # reads lineEdit().placeholderText() only, so on the combo itself the
    # placeholder would be dropped from every language, English included.
    check("the placeholder lands on the line edit",
          c.lineEdit().placeholderText() == "Local folder path...")
    hits = []
    c.editingFinished.connect(lambda: hits.append(1))
    # The forward is GATED on the user having typed - see test_commit_gate,
    # where the whole rule lives. Here: a bare line-edit signal after a
    # programmatic write is NOT a commit, while a direct emit on the class
    # signal (what the pane suites drive) always is.
    c.lineEdit().editingFinished.emit()      # no typing -> not a commit
    check("a bare focus-out after setText does not forward", not hits,
          str(len(hits)))
    c.editingFinished.emit()                 # a test's route
    check("...but the signal still takes a direct emit", len(hits) == 1,
          str(len(hits)))
    c.deleteLater()


def test_combo_remember():
    print("\n== FolderHistoryCombo: remembering ==")
    saved = []
    c = FolderHistoryCombo(cap=3, on_changed=saved.append)
    c.setText("mirrored/by/the/tree")

    check("a blank path is never remembered",
          c.remember("") is False and c.remember(None) is False)
    check("remember reports the change and hands over the whole list",
          c.remember("C:/a/") is True and saved == ["C:/a"], str(saved))
    # remember() is called from a pane's navigate/commit, which writes the box
    # itself a moment later - so a stolen line edit would be invisible in the
    # app and show up only as a first-run oddity.
    check("...without touching the shown text",
          c.text() == "mirrored/by/the/tree", c.text())
    check("the path is stored CANONICAL, not as typed",
          c.itemText(0) == "C:/a", c.itemText(0))

    c.remember("C:/b")
    c.remember("C:/c")
    check("most-recent-first",
          [c.itemText(i) for i in range(c.count())] == ["C:/c", "C:/b", "C:/a"],
          str([c.itemText(i) for i in range(c.count())]))
    # THE rule that keeps the Remote Explorer's startup commit - and every
    # focus-out, since _commit_sync_root deliberately has no unchanged-guard -
    # out of hdfg.cfg.
    n = len(saved)
    check("re-remembering row 0 is not a change, and notifies nobody",
          c.remember("C:/c") is False and len(saved) == n, str(saved[n:]))
    c.remember("C:/a")
    check("an older entry moves to the front rather than duplicating",
          [c.itemText(i) for i in range(c.count())] == ["C:/a", "C:/c", "C:/b"],
          str([c.itemText(i) for i in range(c.count())]))
    c.remember("C:/d")
    check("the cap drops the oldest",
          [c.itemText(i) for i in range(c.count())] == ["C:/d", "C:/a", "C:/c"],
          str([c.itemText(i) for i in range(c.count())]))
    if os.name == "nt":
        before = c.count()
        c.remember("c:/D")
        check("Windows paths differing only in case are ONE entry",
              c.count() == before, f"{before} -> {c.count()}")
    check("the default cap is MAX_PATH_HISTORY",
          FolderHistoryCombo()._cap == MAX_PATH_HISTORY)
    c.deleteLater()


def test_combo_cfg_round_trip():
    print("\n== FolderHistoryCombo: the cfg round trip ==")
    saved = []
    c = FolderHistoryCombo(cap=3, on_changed=saved.append)
    c.setText("typed-free-text")
    c.set_history_from_cfg("C:/x|C:/y")
    # addItem into an EMPTY editable combo sets currentIndex to 0 and
    # overwrites the line edit (measured) - so the restore has to capture and
    # put back what the box was showing.
    check("the restore keeps the shown text",
          c.text() == "typed-free-text", c.text())
    check("the restore populated the list",
          [c.itemText(i) for i in range(c.count())] == ["C:/x", "C:/y"],
          str([c.itemText(i) for i in range(c.count())]))
    check("a restore never echoes back into the file", saved == [], str(saved))

    c.set_history_from_cfg("C:/x|C:/x/|" + '"C:/x"' + "|C:/z")
    check("duplicates that differ only in spelling collapse on restore",
          [c.itemText(i) for i in range(c.count())] == ["C:/x", "C:/z"],
          str([c.itemText(i) for i in range(c.count())]))
    c.set_history_from_cfg("C:/1|C:/2|C:/3|C:/4|C:/5")
    check("the restore honours the cap", c.count() == 3, c.count())

    # UNCONDITIONAL, including for an empty saved value: the list can be
    # emptied now, and skipping the clear would let a second restore
    # resurrect what the user had just forgotten.
    c.set_history_from_cfg("")
    check("an empty saved value clears the list", c.count() == 0, c.count())

    c2 = FolderHistoryCombo(cap=5)
    c2.remember("C:/one")
    c2.remember("C:/two")
    check("history_to_cfg is most-recent-first and '|'-joined",
          c2.history_to_cfg() == "C:/two|C:/one", c2.history_to_cfg())
    c2.addItem("/home/u/a|b")      # forced past remember()'s refusal
    check("a separator-bearing entry is dropped on the way out, not written",
          c2.history_to_cfg() == "C:/two|C:/one", c2.history_to_cfg())
    c.deleteLater()
    c2.deleteLater()


def test_combo_removal():
    print("\n== FolderHistoryCombo: forgetting ==")
    saved, logs = [], []
    c = FolderHistoryCombo(cap=5, on_changed=saved.append, log=logs.append)
    c.set_history_from_cfg("C:/p1|C:/p2|C:/p3")
    c.setText("C:/p2")

    check("the clear button is a CompactButton", c.clear_button is not None
          and type(c.clear_button).__name__ == "CompactButton")
    check("it is live while the shown folder is in the list",
          c.clear_button.isEnabled())
    c.clear_button.click()
    check("it forgot exactly that row",
          [c.itemText(i) for i in range(c.count())] == ["C:/p1", "C:/p3"],
          str([c.itemText(i) for i in range(c.count())]))
    # Removing the CURRENT row rewrites the line edit to the NEIGHBOUR
    # (measured) - so the shown text is restored explicitly. Unlike the image
    # box, forgetting a folder must never move a tree or a sync root.
    check("...and left the box showing the forgotten path", c.text() == "C:/p2",
          c.text())
    check("...and greyed itself out", not c.clear_button.isEnabled())
    check("...and persisted the survivors",
          saved[-1] == "C:/p1|C:/p3", str(saved[-1:]))
    check("...and said so exactly once",
          len(logs) == 1 and "C:/p2" in logs[0], str(logs))

    # Gated on the LIST, not on emptiness as the image box's twin is: this
    # button can only ever forget a row.
    c.setText("C:/not-remembered")
    check("it stays dead for an unlisted folder", not c.clear_button.isEnabled())
    n_saved = len(saved)
    c.clear_button.click()
    check("...and clicking it changes nothing at all",
          c.count() == 2 and len(saved) == n_saved and c.text()
          == "C:/not-remembered")

    check("remove_index refuses an out-of-range row",
          c.remove_index(-1) is False and c.remove_index(99) is False)

    c2 = FolderHistoryCombo(cap=5, clear_button=False)
    check("a box can opt out of the button entirely", c2.clear_button is None)
    c2.set_history_from_cfg("C:/q")
    check("...and its removal path still works", c2.remove_index(0) is True
          and c2.count() == 0)
    c.deleteLater()
    c2.deleteLater()


def test_combo_activation():
    print("\n== FolderHistoryCombo: picking a row ==")
    mirrored = {"p": ""}
    picked = []
    c = FolderHistoryCombo(cap=5, current_path=lambda: mirrored["p"])
    c.pathActivated.connect(picked.append)
    c.set_history_from_cfg("C:/one|C:/two")

    # The Windows phantom activation: the very click that OPENS a popup
    # widened by long paths can also "activate" the row under the cursor. On
    # this box that would re-root the tree (or the sync root) rather than
    # merely reloading something, so the guard is not optional.
    mirrored["p"] = "C:/one"
    c._popup_shown_at = __import__("time").monotonic()
    c.activated.emit(0)
    check("re-picking the path the box already mirrors emits nothing",
          picked == [], str(picked))
    c.activated.emit(1)
    check("a real pick emits the path", picked == ["C:/two"], str(picked))

    # ...but OUTSIDE that window, deliberately picking where you already
    # are is a real gesture and must be passed on (9.7.22): on the Remote
    # Explorer it is how you get BACK to the sync root after browsing
    # elsewhere, which swallowing every such pick made impossible.
    picked.clear()
    c._popup_shown_at = 0.0
    c.activated.emit(0)
    check("a deliberate re-pick of the mirrored path IS passed on",
          picked == ["C:/one"], str(picked))
    # The two panes offer this entry differently: the SD box follows the tree
    # so nothing would ever enter its list by hand, while the sync-root box
    # already shows a deliberate choice.
    check("'Remember this folder' is opt-in, and off by default",
          FolderHistoryCombo().offer_remember is False
          and FolderHistoryCombo(offer_remember=True).offer_remember is True)
    c.deleteLater()


def test_commit_gate():
    """A commit is the user TYPING something, and nothing else (9.7.22).

    Every check here is a regression found in review, after the offscreen and
    widget suites had gone green - because those drive the commit by emitting
    `editingFinished` directly, which is precisely the step these bugs slipped
    past. A QLineEdit only ever emits it after a real edit; a QComboBox's line
    edit also emits it on ANY focus-out and from inside `showPopup()`, so the
    straight signal-to-signal forward this started as turned "I opened the
    dropdown to look" and "I clicked away" into "I committed this path".

    On the Remote Explorer that re-rooted the browse tree and hid the pulsing
    'Set current folder as new sync root folder' offer mid-gesture; on the SD
    Card box it REMEMBERED the folder the user had merely walked to, which is
    the exact rule `local_navigate_to_dir`'s remember=False default exists to
    enforce. Measured on the real platform: offscreen Qt does not reproduce
    either, so these drive the widget's own signals rather than the window.
    """
    print("\n== FolderHistoryCombo: what counts as a commit ==")
    commits = []
    c = FolderHistoryCombo(cap=5)
    c.editingFinished.connect(lambda: commits.append(c.text()))
    c.set_history_from_cfg("C:/proj/alpha|C:/proj/beta")

    # The line edit's own signal is the real widget's route. A focus-out with
    # no typing reaches it exactly like this.
    c.setText("C:/walked/here")            # the pane's mirror of the tree
    c.lineEdit().editingFinished.emit()
    check("a mirror write then a focus-out does NOT commit", commits == [],
          str(commits))

    # textEdited is the user typing - never setText.
    c.lineEdit().setText("C:/typed")       # setText on the LINE EDIT is a user
    c.lineEdit().textEdited.emit("C:/typed")   # ...edit as far as Qt reports
    c.lineEdit().editingFinished.emit()
    check("typing then leaving the box DOES commit", commits == ["C:/typed"],
          str(commits))
    commits.clear()
    c.lineEdit().editingFinished.emit()
    check("...but only once - a second focus-out is silent", commits == [],
          str(commits))

    # The box's own dropdown steals the focus; Qt emits editingFinished from
    # inside showPopup() itself, so the gate cannot rely on the view being
    # visible yet.
    commits.clear()
    c.lineEdit().textEdited.emit("C:/half-typed")
    c._opening_popup = True
    c.lineEdit().editingFinished.emit()
    c._opening_popup = False
    check("the box's own popup opening is not a commit", commits == [],
          str(commits))
    c.showPopup()
    c.lineEdit().editingFinished.emit()
    check("...nor is a focus-out while that popup is up", commits == [],
          str(commits))
    c.hidePopup()
    # The half-typed text is still pending, so closing the popup and leaving
    # the box for real DOES commit it - nothing was silently dropped.
    c.lineEdit().editingFinished.emit()
    check("...and the typed text is still committed afterwards",
          len(commits) == 1, str(commits))

    # Qt's editable-combo default is an INLINE completer, which rewrites what
    # is being typed to the first row that starts with it: "C:/proj/al" left
    # the box reading "C:/proj/alpha", and committing navigated somewhere the
    # user never typed. The QLineEdit these boxes replaced had no completer.
    check("no completer rewrites what is typed", c.completer() is None,
          str(c.completer()))

    # A QComboBox changes row on every wheel notch and reports it as an
    # `activated` - i.e. as a deliberate pick - so a stray scroll would
    # re-root an explorer (or, on the image box, mount another disk).
    picks = []
    c2 = FolderHistoryCombo(cap=5)
    c2.pathActivated.connect(picks.append)
    c2.set_history_from_cfg("C:/one|C:/two")
    c2.setCurrentIndex(0)
    before = c2.currentText()
    c2.wheelEvent(_wheel_event(c2))
    check("a wheel notch over the box changes nothing",
          c2.currentText() == before and picks == [],
          f"{before!r} -> {c2.currentText()!r} {picks}")
    # The same guard covers the SD-image box, which inherits it from the base.
    img = PathHistoryCombo()
    img.setEditable(True)
    img.addItems(["C:/a.img", "C:/b.img"])
    img.setCurrentIndex(0)
    img.wheelEvent(_wheel_event(img))
    check("...and the image box inherits it",
          img.currentText() == "C:/a.img", img.currentText())
    c.deleteLater()
    c2.deleteLater()
    img.deleteLater()


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    test_canonicaliser()
    test_setting_keys()
    test_combo_basics()
    test_combo_remember()
    test_combo_cfg_round_trip()
    test_combo_removal()
    test_combo_activation()
    test_commit_gate()
    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S): " + "; ".join(FAILURES))
        sys.exit(1)
    print("RESULT: ALL PASS")
