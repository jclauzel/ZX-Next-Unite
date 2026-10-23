# -*- coding: utf-8 -*-
"""The RS232 ESP station-address picker (9.7.54), driven with REAL events.

Settings -> "IP address shown to the emulated Next:" is an editable
QComboBox (``zxnu_settings_pane.HostIpCombo``). The editable-combo rules the
folder boxes paid for (``zxnu_pathhistorycombo``, ``tests/test_path_history``)
all apply to it, and its first cut broke one of them: opening the dropdown
re-reads the rows and wiped an address the user was half-way through typing.
A suite that commits by emitting ``editingFinished`` or ``activated`` itself
cannot see any of this - the signal-level echo IS the bug - so every check
here goes through key and mouse events delivered to the widget.

Also covers ``zxnu_config.esp_station_ip_setting``, the saved value's
normaliser, which must agree with the emulation about what an address is.

Run: python tests/test_esp_ip_combo.py  (plain script, like the suite).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt              # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent       # noqa: E402
from PySide6.QtTest import QTest                                    # noqa: E402
from PySide6.QtWidgets import (QApplication, QLineEdit, QVBoxLayout,  # noqa: E402
                               QWidget)

FAILURES = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


app = QApplication.instance() or QApplication(sys.argv)

import espemu                                                       # noqa: E402
from zxnu_config import esp_station_ip_setting                      # noqa: E402
from zxnu_settings_pane import HostIpCombo                          # noqa: E402

ADDRS = ["10.0.0.31", "172.21.48.1", "192.168.202.1"]


class Rig:
    """A combo in a window beside a second field to move the focus to,
    with the three callables the pane would hand it, recorded."""

    def __init__(self, saved=""):
        self.saved = saved
        self.persisted = []
        self.refused = []
        self.win = QWidget()
        lay = QVBoxLayout(self.win)
        self.combo = HostIpCombo(
            saved=lambda: self.saved,
            persist=self._persist,
            refused=self.refused.append,
            addresses=lambda: list(ADDRS),
            primary=lambda: ADDRS[0])
        self.other = QLineEdit()
        lay.addWidget(self.combo)
        lay.addWidget(self.other)
        self.win.show()
        QTest.qWaitForWindowExposed(self.win)
        self.win.activateWindow()
        settle()

    def _persist(self, value):
        self.persisted.append(value)
        self.saved = value

    def type_in(self, text):
        le = self.combo.lineEdit()
        le.setFocus()
        settle()
        le.selectAll()
        QTest.keyClick(le, Qt.Key.Key_Backspace)
        QTest.keyClicks(le, text)
        settle()

    def focus_away(self):
        self.other.setFocus()
        settle()

    def click_row(self, row, button=Qt.MouseButton.LeftButton, hasty=False):
        """Open the dropdown and click *row* the way a user does: move onto
        it, then press and release. QComboBox's popup IGNORES a release that
        comes too soon after it opened with no mouse move (measured on
        Windows: it guards against the click that opened it), so a realistic
        gesture waits and moves first; ``hasty`` skips both."""
        self.combo.showPopup()
        settle()
        view = self.combo.view()
        rect = view.visualRect(self.combo.model().index(row, 0))
        spot = QPointF(min(rect.center().x(), view.viewport().width() - 5),
                       rect.center().y())
        glob = view.viewport().mapToGlobal(spot.toPoint()).toPointF()
        if not hasty:
            QTest.qWait(QApplication.doubleClickInterval() + 50)
            app.sendEvent(view.viewport(), QMouseEvent(
                QEvent.Type.MouseMove, spot, glob, Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        for etype in (QEvent.Type.MouseButtonPress,
                      QEvent.Type.MouseButtonRelease):
            app.sendEvent(view.viewport(), QMouseEvent(
                etype, spot, glob, button, button,
                Qt.KeyboardModifier.NoModifier))
        settle()

    def close(self):
        self.combo.hidePopup()
        self.win.close()
        self.win.deleteLater()
        settle()


def settle():
    for _ in range(3):
        app.processEvents()
        QTest.qWait(10)


def rows(combo):
    return [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]


# ---------------------------------------------------------------------------
def test_normaliser():
    print("\n== esp_station_ip_setting: the saved value's one rule ==")
    for raw, want in (("", ""), (None, ""), ("  10.0.0.5 ", "10.0.0.5"),
                      ("127.0.0.1", "127.0.0.1"), ("0.0.0.0", ""),
                      ("255.255.255.255", ""), ("224.0.0.1", ""),
                      ("10.0.0.05", ""), ("lan", ""), ("10.0.0.5:2048", ""),
                      ("::1", "")):
        got = esp_station_ip_setting(raw)
        check(f"{raw!a} -> {want!a}", got == want, repr(got))
    for raw in ("10.0.0.5", "0.0.0.0", "1.2.3.4", "240.1.1.1", "lan"):
        check(f"the normaliser and the emulation agree on {raw!a}",
              bool(esp_station_ip_setting(raw)) == espemu.is_station_ipv4(raw))


def test_rows_and_restore():
    print("\n== HostIpCombo: the rows, and a saved address the PC lacks ==")
    rig = Rig(saved="")
    c = rig.combo
    check("rows: Automatic first (data ''), the PC's addresses, loopback last",
          rows(c) == [(f"Automatic ({ADDRS[0]})", "")]
          + [(a, a) for a in ADDRS] + [("127.0.0.1", "127.0.0.1")], rows(c))
    check("the saved automatic choice is shown", c.currentIndex() == 0
          and c.currentText() == f"Automatic ({ADDRS[0]})")
    rig.saved = "10.77.0.1"
    c.show_value(rig.saved)
    check("a saved address the list lacks is ADDED and shown",
          c.currentData() == "10.77.0.1" and c.currentText() == "10.77.0.1")
    c.refresh()
    check("...and survives a refresh", c.currentData() == "10.77.0.1",
          rows(c))
    check("the editable-combo basics: NoInsert, no completer",
          c.insertPolicy() == HostIpCombo.NoInsert and c.completer() is None)
    check("nothing was persisted by building, showing or refreshing",
          rig.persisted == [] and rig.refused == [])
    rig.close()


def test_typed_commits():
    print("\n== HostIpCombo: a typed address, through real keys ==")
    rig = Rig()
    c = rig.combo
    rig.type_in("10.9.9.9")
    check("typing alone commits nothing", rig.persisted == [])
    QTest.keyClick(c.lineEdit(), Qt.Key.Key_Return)
    settle()
    check("Return commits the typed address once",
          rig.persisted == ["10.9.9.9"], rig.persisted)
    check("...shown as a row afterwards, nothing inserted twice",
          c.currentData() == "10.9.9.9"
          and [d for _t, d in rows(c)].count("10.9.9.9") == 1, rows(c))
    rig.focus_away()
    check("leaving the box after that commits nothing more",
          rig.persisted == ["10.9.9.9"], rig.persisted)

    rig.type_in(" 10.8.8.8 ")
    rig.focus_away()
    check("typing then leaving the box commits it (spaces stripped)",
          rig.persisted[-1:] == ["10.8.8.8"], rig.persisted)

    before = list(rig.persisted)
    rig.type_in("10.8.8.8")
    QTest.keyClick(c.lineEdit(), Qt.Key.Key_Return)
    settle()
    check("re-typing the saved address persists nothing (idempotent)",
          rig.persisted == before, rig.persisted)

    for bad in ("0.0.0.0", "lan", "10.0.0", "255.255.255.255"):
        rig.type_in(bad)
        QTest.keyClick(c.lineEdit(), Qt.Key.Key_Return)
        settle()
        check(f"{bad!a} is refused, told, and the saved value shown again",
              rig.persisted == before and rig.refused[-1:] == [bad]
              and c.currentText() == "10.8.8.8",
              (rig.persisted, rig.refused, c.currentText()))
    n = len(rig.refused)
    rig.focus_away()
    check("...and a refusal is said once, not again on the next focus-out",
          len(rig.refused) == n, rig.refused)

    rig.type_in("")
    QTest.keyClick(c.lineEdit(), Qt.Key.Key_Return)
    settle()
    check("an emptied box means automatic",
          rig.persisted[-1:] == [""] and c.currentIndex() == 0,
          (rig.persisted, c.currentText()))

    rig.type_in(f"Automatic ({ADDRS[0]})")
    rig.type_in("172.21.48.1")
    QTest.keyClick(c.lineEdit(), Qt.Key.Key_Return)
    settle()
    check("typing a listed address commits that address (not automatic)",
          rig.persisted[-1:] == ["172.21.48.1"], rig.persisted)
    rig.close()


def test_popup_keeps_typing():
    print("\n== HostIpCombo: opening the list keeps a half-typed address ==")
    rig = Rig(saved="127.0.0.1")
    c = rig.combo
    rig.type_in("10.0.0.9")
    c.showPopup()
    settle()
    check("the typed text survives the dropdown re-reading its rows",
          c.lineEdit().text() == "10.0.0.9", c.lineEdit().text())
    check("opening the dropdown commits nothing (editingFinished from "
          "inside showPopup)", rig.persisted == [] and rig.refused == [],
          (rig.persisted, rig.refused))
    QTest.keyClick(c.view(), Qt.Key.Key_Escape)
    settle()
    check("Escape closes it without committing or refusing",
          rig.persisted == [] and rig.refused == [],
          (rig.persisted, rig.refused))
    check("...and the typed text is still there",
          c.lineEdit().text() == "10.0.0.9", c.lineEdit().text())
    c.lineEdit().setFocus()
    settle()
    rig.focus_away()
    check("leaving the box then commits what was typed",
          rig.persisted == ["10.0.0.9"], rig.persisted)

    rig.type_in("10.0")                    # half-typed and not an address
    c.showPopup()
    settle()
    QTest.keyClick(c.view(), Qt.Key.Key_Escape)
    settle()
    check("a half-typed '10.0' is not refused just for looking at the list",
          rig.refused == [], rig.refused)
    rig.close()


def test_picks():
    print("\n== HostIpCombo: a pick is a gesture, not an activated() ==")
    rig = Rig()
    c = rig.combo
    rig.click_row(2)
    check("a left click on a row persists that row",
          rig.persisted == [ADDRS[1]], rig.persisted)
    rig.click_row(0)
    check("picking Automatic persists ''", rig.persisted[-1:] == [""],
          rig.persisted)
    n = len(rig.persisted)
    rig.click_row(3, Qt.MouseButton.RightButton)
    c.hidePopup()
    settle()
    check("a right click on a row neither persists nor selects it",
          len(rig.persisted) == n and c.currentIndex() == 0,
          (rig.persisted, c.currentText()))
    rig.click_row(3, hasty=True)
    c.hidePopup()
    settle()
    check("a release the popup may ignore leaves no pick armed for later",
          c._pick_armed is False, c._pick_armed)
    n = len(rig.persisted)
    c.activated.emit(3)
    settle()
    check("a bare activated() (the Escape / focus-out echo) persists nothing",
          len(rig.persisted) == n, rig.persisted)
    c.showPopup()
    settle()
    view = c.view()
    view.setCurrentIndex(c.model().index(4, 0))
    app.sendEvent(view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                                  Qt.KeyboardModifier.NoModifier))
    settle()
    check("Return on the highlighted row persists it",
          rig.persisted[-1:] == ["127.0.0.1"], rig.persisted)
    before = c.currentIndex()
    app.sendEvent(c, QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False))
    settle()
    check("a wheel notch over the closed box changes nothing",
          c.currentIndex() == before and rig.persisted[-1:] == ["127.0.0.1"],
          (c.currentText(), rig.persisted))
    rig.close()


def test_retranslate():
    print("\n== HostIpCombo: the Automatic row follows a language switch ==")
    import zxnu_i18n
    rig = Rig()
    c = rig.combo
    lang = getattr(zxnu_i18n, "_current_ui_language", "en")
    try:
        zxnu_i18n.set_current_ui_language("fr")
        c.retranslate()
        check("the Automatic row is rewritten in the new language",
              c.itemText(0) == f"Automatique ({ADDRS[0]})"
              and c.currentText() == c.itemText(0), c.itemText(0))
        check("...and still carries '' (the setting is language-free)",
              c.itemData(0) == "")
    finally:
        zxnu_i18n.set_current_ui_language(lang)
        c.retranslate()
    check("...and back", c.itemText(0) == f"Automatic ({ADDRS[0]})",
          c.itemText(0))
    check("a language switch persists nothing", rig.persisted == [])
    rig.close()


def main():
    print(f"Qt platform: {QApplication.platformName()}")
    test_normaliser()
    test_rows_and_restore()
    test_typed_commits()
    test_popup_keeps_typing()
    test_picks()
    test_retranslate()
    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S): " + "; ".join(FAILURES))
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
