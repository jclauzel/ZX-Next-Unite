"""The Settings tab's Basic / Advanced mode and its group boxes (9.7.63).

Feedback said the tab was too complex. It now opens in BASIC mode - the rows
most people need (``SETTINGS_BASIC_ROWS`` in ``zxnu_settings_pane``, Julien's
list) - and a flip switch at its top right, above the scroll area, shows
every row. The MAME settings, the CSpect settings and the RS232 ESP
emulation they share each sit in a group box, like the Launch Mame / Launch
CSpect groups of the SD Card tab; a group's rows are CONTIGUOUS in
``SETTINGS_TAB_ROWS``, the box takes their place in the main grid, and
``settings_grid_row`` answers a grouped row's index INSIDE its box. And a
MAME installed or updated in-app is followed by the question the CSpect
install asks: enable the RS232 ESP emulation for it?

Covered headlessly here (CI has no hdfmonkey; this needs none):

* the registrar tables: groups contiguous, the in-group rows and the group
  keys, the anchors, the basic set pinned, unknown names refused;
* ``SettingsModeSwitch``: a click and Space toggle it and emit ``toggled``,
  it paints offscreen;
* ``_grid_row_widgets``: nested layouts reached, a container widget's
  children left alone;
* the wiring: the key persisted and decoded, the page the main window tabs,
  the config restore's entry, the ONE placement call, the MAME offer
  scheduled by ``_mame_finish_install`` and gated on the box, and every new
  text in all six catalogs.

The real tab - the restore from the cfg, the click, what hides and what
stays, the boxes - is driven in offscreen phase 7; the offer's dialogs in
phase 12 (tests/test_ui_offscreen.py).

Run: python tests/test_settings_basic_advanced.py  (no pytest, matching the suite).
"""
import ast
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QGridLayout,  # noqa: E402
                               QHBoxLayout, QLabel, QVBoxLayout, QWidget)

import zxnu_settings_pane as sp  # noqa: E402
from zxnu_config import (CONFIG_FILE_SETTINGS,  # noqa: E402
                         SETTING_SETTINGS_ADVANCED, mame_rs232_esp_enabled,
                         settings_advanced_enabled)
from zxnu_i18n import CATALOGS, UI_LANGUAGES, _TOAST_CATALOGS  # noqa: E402

FAIL = []
LANGS = [code for code, _n in UI_LANGUAGES if code != "en"]


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def _src(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as fh:
        return fh.read()


def _func_source(tree, source, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


# ---------------------------------------------------------------- tables
def test_tables():
    print("\n== registrar tables ==")
    rows = list(sp.SETTINGS_TAB_ROWS)
    groups = {key: (title, members) for key, title, members in sp.SETTINGS_TAB_GROUPS}
    check("three group boxes: MAME, CSpect, the shared RS232 ESP emulation",
          list(groups) == ["mame", "cspect", "rs232_esp"]
          and groups["mame"][0] == "MAME" and groups["cspect"][0] == "CSpect"
          and groups["rs232_esp"][0] == "RS232 ESP emulation (MAME and CSpect)",
          str(groups))
    for key, (_title, members) in groups.items():
        first = rows.index(members[0])
        check(f"group {key!r}: its rows are contiguous in SETTINGS_TAB_ROWS",
              tuple(rows[first:first + len(members)]) == tuple(members),
              str(rows[first:first + len(members)]))
        check(f"group {key!r}: settings_grid_row answers the index INSIDE the box",
              [sp.settings_grid_row(m) for m in members] == list(range(len(members)))
              and all(sp.settings_grid_group(m) == key for m in members))
    check("the MAME box holds the MAME rows, the RS232 ESP master included",
          groups["mame"][1] == ("mame_rom", "mame_params", "mame_update_check",
                                "mame_rs232_esp"))
    check("the CSpect box holds the CSpect rows, its RS232 ESP master last",
          groups["cspect"][1] == ("cspect_params", "cspect_mcp", "cspect_update_check",
                                  "cspect_discard_restricted_updates",
                                  "cspect_rs232_esp"))
    check("the shared box: port, address, verbose",
          groups["rs232_esp"][1] == ("rs232_esp_port", "rs232_esp_ip",
                                     "rs232_esp_verbose"))
    # Anchors: a group takes ONE main-grid row where its first member would
    # have been, every ungrouped row after it moves up by the rest.
    main_row = 0
    expected = {}
    i = 0
    while i < len(rows):
        name = rows[i]
        g = sp.settings_grid_group(name)
        if g:
            expected[g] = main_row
            i += len(groups[g][1])
        else:
            check(f"{name}: main-grid row {main_row}",
                  sp.settings_grid_row(name) == main_row,
                  str(sp.settings_grid_row(name)))
            i += 1
        main_row += 1
    check("the anchors are where the first members would have been",
          all(sp.settings_group_anchor_row(k) == v for k, v in expected.items())
          and sp.settings_group_anchor_row("mame") + 1
          == sp.settings_group_anchor_row("cspect")
          and sp.settings_group_anchor_row("cspect") + 1
          == sp.settings_group_anchor_row("rs232_esp"),
          str({k: sp.settings_group_anchor_row(k) for k in groups}))
    check("rows before the groups keep their flat index",
          sp.settings_grid_row("zxnextremote_update_check") == 0
          and sp.settings_grid_row("no_emulator_toast")
          == rows.index("no_emulator_toast"))
    check("rows after the groups moved up by the grouped rows minus the boxes",
          sp.settings_grid_row("open_config_file")
          == rows.index("open_config_file") - sum(len(m) - 1 for _t, m in groups.values()))
    for bad in ("", "nope", "mame"):
        try:
            sp.settings_grid_row(bad)
            ok = False
        except ValueError:
            ok = True
        check(f"settings_grid_row({bad!r}) raises ValueError", ok)
    try:
        sp.settings_grid_group("nope")
        ok = False
    except ValueError:
        ok = True
    check("settings_grid_group('nope') raises ValueError", ok)
    check("settings_group_members answers the tuple",
          sp.settings_group_members("cspect") == groups["cspect"][1])

    # The basic set: Julien's list, pinned.
    basic = set(sp.SETTINGS_BASIC_ROWS)
    check("every basic row is a registered row", basic <= set(rows), str(basic - set(rows)))
    check("the basic set is the asked one", basic == {
        "zxnu_update_check", "ui_language", "wizard", "desktop_theme",
        "delete_to_recycle_bin", "re_autostart", "nextsync_verify_crc",
        "search_sort", "general_font", "retro_log_font", "mame_rom", "mame_update_check",
        "mame_rs232_esp", "cspect_update_check", "cspect_discard_restricted_updates",
        "cspect_rs232_esp", "rs232_esp_port", "rs232_esp_ip", "itchio_tab",
        "http_bridge", "http_bridge_trace", "mcp_server", "mcp_server_hint"},
        str(sorted(basic)))
    check("the two 'Require bearer token' rows wait behind Advanced (Julien, on the first cut)",
          not ({"http_bridge_token", "mcp_server_token"} & basic)
          and "http_bridge_token" in rows and "mcp_server_token" in rows)
    check("every group keeps at least one basic row (no box vanishes in Basic)",
          all(set(m) & basic for _t, m in groups.values()))
    check("the verbose switch, the parameters, the colours wait behind Advanced (the MAME ROM is Basic since 9.7.64)",
          "mame_rom" in basic and
          not ({"rs232_esp_verbose", "mame_params", "cspect_params",
                "color_background", "reset_theme", "crash_log"} & basic))


# ---------------------------------------------------------------- widget
def test_switch():
    print("\n== SettingsModeSwitch ==")
    app = QApplication.instance() or QApplication(sys.argv)
    sw = sp.SettingsModeSwitch()
    sw.show()
    app.processEvents()
    got = []
    sw.toggled.connect(lambda on: got.append(on))
    check("checkable, off by default", sw.isCheckable() and not sw.isChecked())
    sw.click()
    check("a click turns it on and says so", sw.isChecked() and got == [True])
    pm = QPixmap(sw.size())
    pm.fill(Qt.transparent)
    sw.render(pm)
    check("it paints (on)", not pm.isNull() and pm.width() == sw.sizeHint().width())
    QTest.keyClick(sw, Qt.Key_Space)
    app.processEvents()
    check("Space turns it off again", not sw.isChecked() and got == [True, False])
    sw.render(pm)
    check("it paints (off, focused)", not pm.isNull())
    sw.setEnabled(False)
    sw.render(pm)
    sw.click()
    check("disabled: a click does nothing, and it still paints",
          not sw.isChecked() and got == [True, False])
    sw.setEnabled(True)
    sw.setChecked(True)
    check("setChecked drives it like any button", sw.isChecked() and got[-1] is True)
    sw.deleteLater()


# -------------------------------------------------------- row widgets
def test_row_widgets():
    print("\n== _grid_row_widgets ==")
    QApplication.instance() or QApplication(sys.argv)
    w = QWidget()
    g = QGridLayout(w)
    a = QLabel("a")
    g.addWidget(a, 0, 0)
    b = QCheckBox("b")
    c = QCheckBox("c")
    h = QHBoxLayout()
    h.addWidget(b)
    h.addStretch(1)
    v = QVBoxLayout()
    v.addWidget(c)
    h.addLayout(v)
    g.addLayout(h, 1, 0, 1, 2)
    box = QWidget()
    QLabel("inner", box)
    g.addWidget(box, 2, 0)
    check("a widget placed directly", sp._grid_row_widgets(g, 0) == [a])
    check("widgets inside nested layouts, spacers skipped",
          sp._grid_row_widgets(g, 1) == [b, c])
    check("a container widget, never its children", sp._grid_row_widgets(g, 2) == [box])
    check("an empty row answers nothing", sp._grid_row_widgets(g, 3) == [])
    check("the hidden-by-mode property name is a string",
          isinstance(sp.SETTINGS_MODE_HIDDEN_PROP, str) and sp.SETTINGS_MODE_HIDDEN_PROP)


# ---------------------------------------------------------------- wiring
def test_wiring():
    print("\n== wiring ==")
    check("the setting is persisted", SETTING_SETTINGS_ADVANCED in CONFIG_FILE_SETTINGS)
    check("decoder: Basic by default, Advanced only on an explicit true",
          not settings_advanced_enabled({}) and not settings_advanced_enabled(None)
          and settings_advanced_enabled({SETTING_SETTINGS_ADVANCED: "true"})
          and not settings_advanced_enabled({SETTING_SETTINGS_ADVANCED: "maybe"}))
    check("the MAME RS232 ESP decoder reads the launch key the same way",
          not mame_rs232_esp_enabled({}) and mame_rs232_esp_enabled({"mame_rs232_esp": "on"}))

    pane = _src("zxnu_settings_pane.py")
    raw_writes = [ln for ln in pane.splitlines() if "grid_tab_Settings.add" in ln]
    check("the raw main grid is written ONCE: the group boxes' anchors",
          len(raw_writes) == 1, str(raw_writes))
    check("every row goes through _place()", pane.count("_place(") >= 70,
          str(pane.count("_place(")))
    check("the main grid ends in a stretch row (a short Basic page never spreads the boxes)",
          "grid_tab_Settings.setRowStretch(" in pane)
    check("the pane applies Basic at build and exposes the restore entry",
          "_settings_apply_mode(False)" in pane
          and "host._settings_set_advanced = _settings_set_advanced" in pane
          and "host.settings_tab_page.tab_name_private = \"Settings\"" in pane)
    main = _src("zxnu_main.py")
    check("the main window tabs the page, not the bare scroll area",
          'wid_inner.tab.addTab(self.settings_tab_page, "Settings' in main
          and "addTab(settings_scroll" not in main)
    cio = _src("zxnu_config_io.py")
    check("the config restore sets the mode through the no-save entry",
          "host._settings_set_advanced(" in cio and "settings_advanced_enabled(" in cio)

    emu = _src("zxnu_emulator_ops.py")
    tree = ast.parse(emu)
    finish = _func_source(tree, emu, "_mame_finish_install")
    offer = _func_source(tree, emu, "_offer_mame_esp_emulation")
    check("_mame_finish_install schedules the MAME RS232 ESP offer",
          "_offer_mame_esp_emulation" in finish and "QTimer.singleShot" in finish)
    check("the offer is gated on the box being off and ticks it through its handler",
          "mame_rs232_esp_enabled(configuration_dictionary)" in offer
          and "settings_rs232_esp_checkbox" in offer and "setChecked(True)" in offer
          and "activeModalWidget()" in offer)
    check("the offer is exposed on the host",
          "host._offer_mame_esp_emulation = _offer_mame_esp_emulation" in emu)

    texts = ["Basic", "Advanced", "MAME", "CSpect",
             "RS232 ESP emulation (MAME and CSpect)",
             "Enable the RS232 ESP emulation for MAME?",
             "RS232 ESP: the emulation is now enabled for MAME - the next MAME "
             "launched from the app gives the emulated Next network access."]
    for code in LANGS:
        missing = [t for t in texts if t not in CATALOGS[code]]
        check(f"{code}: every new label / dialog text is catalogued",
              not missing, str(missing))
        body = [k for k in CATALOGS[code]
                if k.startswith("ZX Next Unite can give the ZX Spectrum Next emulated by MAME")]
        check(f"{code}: the offer's body is catalogued and keeps its markup",
              len(body) == 1 and "<b>esp8266_at</b>" in CATALOGS[code][body[0]]
              and "<br><br>" in CATALOGS[code][body[0]], str(body))
        check(f"{code}: the toast title is catalogued",
              "✅  RS232 ESP emulation enabled for MAME" in _TOAST_CATALOGS[code])
        check(f"{code}: the Help line is catalogued",
              any(k.startswith("The Settings tab opens in Basic mode") for k in CATALOGS[code]))


def main():
    test_tables()
    test_switch()
    test_row_widgets()
    test_wiring()
    print()
    if FAIL:
        print(f"FAILED: {len(FAIL)}")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
